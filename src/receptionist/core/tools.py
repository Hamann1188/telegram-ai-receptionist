"""Tools Claude can call: strict JSON schemas and their handlers.

Every tool result is a JSON object. Errors come back as `is_error` results with a
message the model can act on; nothing here raises to the agent loop.
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from receptionist.core.clinic import Clinic, Service
from receptionist.core.ports import (
    BookingRecord,
    BookingRepository,
    HandoffRepository,
    NewBooking,
    Notifier,
    SlotTaken,
)
from receptionist.core.slots import free_slots

logger = logging.getLogger(__name__)

MAX_SEARCH_DAYS = 14
MAX_SLOTS_RETURNED = 60
MAX_ALTERNATIVES = 6
HANDOFF_REASONS = (
    "patient_request",
    "complaint",
    "medical_question",
    "emergency",
    "not_understood",
    "other",
)
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ToolContext:
    """Everything a tool needs for one chat."""

    chat_id: int
    clinic: Clinic
    bookings: BookingRepository
    handoffs: HandoffRepository
    notifier: Notifier
    clock: Callable[[], datetime] = utc_now


@dataclass(frozen=True)
class ToolResult:
    content: str  # JSON text, sent back as the tool_result content
    is_error: bool = False
    handoff: bool = False  # the chat now belongs to a human operator


class ToolInputError(Exception):
    """Bad tool input; the message is returned to the model."""


# --- schemas -----------------------------------------------------------------


def tool_definitions(clinic: Clinic) -> list[dict]:
    """Tool definitions for the Messages API.

    Strict mode: every object sets `additionalProperties: false` and lists every
    property in `required`; numeric ranges and string lengths aren't supported there,
    so the handlers check them. The output depends only on the clinic profile, so it
    stays byte-identical across requests (prompt cache, fixed tools per session).
    """
    service_ids = [s.id for s in clinic.services]
    service_id = {
        "type": "string",
        "enum": service_ids,
        "description": "Service id from list_services.",
    }
    return [
        _tool(
            "get_clinic_info",
            "Get the clinic's official answer on one topic: opening hours, address and "
            "contacts, parking, payment, installments, insurance, discounts, cancellation "
            "rules, arrival, children, visit preparation, aftercare, warranty or urgent "
            "pain. Use it for any factual question about the clinic instead of answering "
            "from memory.",
            {
                "topic": {
                    "type": "string",
                    "enum": sorted(clinic.faq),
                    "description": "The topic to look up.",
                }
            },
        ),
        _tool(
            "list_services",
            "List the services that can be booked through this chat, with duration, price "
            "in UZS, the specialist and the weekdays the specialist works. Use it before "
            "quoting a price or picking a service_id.",
            {},
        ),
        _tool(
            "find_free_slots",
            "Find free appointment times for a service, in the clinic's local time "
            "(Asia/Tashkent). Search 1-14 days starting at date_from. Offer the patient a "
            "few of the returned times; never invent a time that isn't in the result.",
            {
                "service_id": service_id,
                "date_from": {
                    "type": "string",
                    "format": "date",
                    "description": "First day to search, YYYY-MM-DD, in the clinic's time zone.",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days to search, from 1 to 14.",
                },
            },
        ),
        _tool(
            "create_booking",
            "Book an appointment. Call it with confirmed=false to check a time before "
            "asking the patient; it never books then. Book only after reading back the "
            "service, date, time, name and phone and getting the patient's explicit yes in "
            "their latest message; then call again with confirmed=true.",
            {
                "service_id": service_id,
                "date": {
                    "type": "string",
                    "format": "date",
                    "description": "Appointment date, YYYY-MM-DD, clinic local time.",
                },
                "time": {
                    "type": "string",
                    "pattern": "^\\d{2}:\\d{2}$",
                    "description": "Start time HH:MM, clinic local time, from find_free_slots.",
                },
                "patient_name": {
                    "type": "string",
                    "description": "The patient's name as they gave it.",
                },
                "phone": {
                    "type": "string",
                    "description": "Contact phone number as the patient gave it.",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "True only after the patient explicitly confirmed all details.",
                },
            },
        ),
        _tool(
            "list_my_bookings",
            "List this chat's upcoming appointments with their booking ids.",
            {},
        ),
        _tool(
            "cancel_booking",
            "Cancel one of this chat's upcoming appointments. Confirm with the patient "
            "first. Cancelling less than 24 hours ahead costs a fee; the result says so.",
            {
                "booking_id": {
                    "type": "integer",
                    "description": "Booking id from list_my_bookings or create_booking.",
                }
            },
        ),
        _tool(
            "handoff_to_human",
            "Hand the chat to a staff member. Use it when the patient asks for a person, "
            "complains, asks a medical question beyond the clinic information, describes "
            "an emergency, or when you can't help after two attempts. After it, tell the "
            "patient a staff member will reply here, and call no more tools.",
            {
                "reason": {
                    "type": "string",
                    "enum": list(HANDOFF_REASONS),
                    "description": "Why the chat needs a person.",
                },
                "summary": {
                    "type": "string",
                    "description": "Two or three sentences for the staff member: who the "
                    "patient is, what they want, what was already done. In English.",
                },
            },
        ),
    ]


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


# --- dispatch ----------------------------------------------------------------

Handler = Callable[[dict, ToolContext], Awaitable["ToolResult"]]


async def run_tool(name: str, tool_input: object, ctx: ToolContext) -> ToolResult:
    handler = HANDLERS.get(name)
    if handler is None:
        return _error(f"Unknown tool {name!r}.")
    if not isinstance(tool_input, dict):
        return _error("Tool input must be a JSON object.")
    try:
        return await handler(tool_input, ctx)
    except ToolInputError as exc:
        return _error(str(exc))
    except Exception:
        logger.exception("Tool %s failed", name)
        return _error(
            "Internal error while running the tool. Apologise and offer to connect the "
            "patient with a staff member."
        )


# --- handlers ----------------------------------------------------------------


async def get_clinic_info(tool_input: dict, ctx: ToolContext) -> ToolResult:
    topic = _enum(tool_input, "topic", sorted(ctx.clinic.faq))
    return _ok({"topic": topic, "answer": ctx.clinic.faq[topic]})


async def list_services(tool_input: dict, ctx: ToolContext) -> ToolResult:
    return _ok(
        {
            "currency": "UZS",
            "services": [_service_info(ctx.clinic, s) for s in ctx.clinic.services],
        }
    )


async def find_slots(tool_input: dict, ctx: ToolContext) -> ToolResult:
    clinic = ctx.clinic
    service = _service(tool_input, ctx)
    date_from = _date(tool_input, "date_from")
    days = _int(tool_input, "days")
    if not 1 <= days <= MAX_SEARCH_DAYS:
        raise ToolInputError(f"days must be between 1 and {MAX_SEARCH_DAYS}.")

    now = ctx.clock()
    today = now.astimezone(clinic.tz).date()
    start_day = max(date_from, today)
    last_day = today + timedelta(days=clinic.booking.horizon_days)
    result: dict = {
        "service_id": service.id,
        "service_name": service.name.model_dump(),
        "duration_minutes": service.duration_minutes,
        "timezone": clinic.timezone,
        "today": today.isoformat(),
        "days": [],
    }
    if start_day > last_day:
        result["note"] = (
            f"Bookings open {clinic.booking.horizon_days} days ahead; the last bookable "
            f"date is {last_day.isoformat()}."
        )
        return _ok(result)

    slots = await _free(ctx, service, start_day, days)
    truncated = len(slots) > MAX_SLOTS_RETURNED
    by_day: dict[date, list[str]] = {}
    for slot in slots[:MAX_SLOTS_RETURNED]:
        by_day.setdefault(slot.date(), []).append(slot.strftime("%H:%M"))
    result["days"] = [
        {"date": d.isoformat(), "weekday": WEEKDAY_NAMES[d.weekday()], "times": times}
        for d, times in by_day.items()
    ]
    if truncated:
        result["note"] = "More times exist after the last one listed; search from a later date."
    elif not slots:
        result["note"] = "No free times in this period. Try later dates."
    return _ok(result)


async def create_booking(tool_input: dict, ctx: ToolContext) -> ToolResult:
    clinic = ctx.clinic
    service = _service(tool_input, ctx)
    day = _date(tool_input, "date")
    start_time = _time(tool_input, "time")
    name = _patient_name(tool_input)
    phone = normalize_phone(_str(tool_input, "phone"))
    confirmed = tool_input.get("confirmed")
    if not isinstance(confirmed, bool):
        raise ToolInputError("confirmed must be true or false.")

    now = ctx.clock()
    upcoming = await ctx.bookings.upcoming_for_chat(ctx.chat_id, now)
    limit = clinic.booking.max_upcoming_per_chat
    if len(upcoming) >= limit:
        return _error(
            f"This chat already has {len(upcoming)} upcoming appointments, the most allowed "
            "through the bot. Offer to cancel one, or hand the chat to a staff member."
        )

    start = datetime.combine(day, start_time, clinic.tz)
    free = await _free(ctx, service, day, 1)
    if start not in free:
        return _error(
            f"{day.isoformat()} {start_time:%H:%M} is not available for this service.",
            free_times_that_day=_nearest(free, start),
        )
    if not confirmed:
        return _error(
            "Not booked yet: the time is free. Read the service, date, time, name and phone "
            "back to the patient, and call create_booking with confirmed=true only after "
            "they explicitly agree."
        )

    booking = NewBooking(
        chat_id=ctx.chat_id,
        service_id=service.id,
        resource=service.resource,
        slot_start=start,
        slot_end=start + timedelta(minutes=service.duration_minutes),
        patient_name=name,
        phone=phone,
    )
    try:
        record = await ctx.bookings.create(booking)
    except SlotTaken:
        free = await _free(ctx, service, day, 1)
        return _error(
            "Another patient booked this time a moment ago. Nothing was booked.",
            free_times_that_day=_nearest(free, start),
        )
    await _notify(ctx.notifier.booking_created(record))
    return _ok(
        {
            "booked": True,
            "booking": _booking_info(clinic, record),
            "remind_patient": [clinic.faq["arrival"], clinic.faq["cancellation"]],
        }
    )


async def list_my_bookings(tool_input: dict, ctx: ToolContext) -> ToolResult:
    upcoming = await ctx.bookings.upcoming_for_chat(ctx.chat_id, ctx.clock())
    return _ok({"bookings": [_booking_info(ctx.clinic, b) for b in upcoming]})


async def cancel_booking(tool_input: dict, ctx: ToolContext) -> ToolResult:
    booking_id = _int(tool_input, "booking_id")
    now = ctx.clock()
    record = await ctx.bookings.cancel(ctx.chat_id, booking_id, now)
    if record is None:
        return _error(
            f"This chat has no upcoming booking with id {booking_id}. Call list_my_bookings "
            "to see the patient's bookings."
        )
    result: dict = {"cancelled": True, "booking": _booking_info(ctx.clinic, record)}
    rules = ctx.clinic.booking
    if record.slot_start - now < timedelta(hours=rules.cancellation_notice_hours):
        result["late_cancellation"] = {
            "fee_uzs": rules.late_cancellation_fee_uzs,
            "policy": ctx.clinic.faq["cancellation"],
        }
    await _notify(ctx.notifier.booking_cancelled(record))
    return _ok(result)


async def handoff_to_human(tool_input: dict, ctx: ToolContext) -> ToolResult:
    reason = _enum(tool_input, "reason", HANDOFF_REASONS)
    summary = _str(tool_input, "summary").strip()[:1000]
    if not summary:
        raise ToolInputError("summary must not be empty.")
    handoff_id = await ctx.handoffs.open(ctx.chat_id, reason, summary)
    await _notify(ctx.notifier.handoff_requested(ctx.chat_id, handoff_id, reason, summary))
    return ToolResult(
        content=_json(
            {
                "handed_off": True,
                "next_step": "Tell the patient a staff member will reply in this chat soon.",
            }
        ),
        handoff=True,
    )


HANDLERS: dict[str, Handler] = {
    "get_clinic_info": get_clinic_info,
    "list_services": list_services,
    "find_free_slots": find_slots,
    "create_booking": create_booking,
    "list_my_bookings": list_my_bookings,
    "cancel_booking": cancel_booking,
    "handoff_to_human": handoff_to_human,
}


# --- helpers -----------------------------------------------------------------


def normalize_phone(raw: str) -> str:
    """E.164 form. A 9-digit number is taken as Uzbek (+998)."""
    compact = re.sub(r"[\s\-().]", "", raw)
    has_plus = compact.startswith("+")
    digits = compact.removeprefix("+")
    if not digits.isdigit():
        raise ToolInputError("phone must contain only digits, spaces, dashes and a leading +.")
    if len(digits) == 9 and not has_plus:
        digits = "998" + digits
    if digits.startswith("998") and len(digits) != 12:
        raise ToolInputError("An Uzbek phone number has 9 digits after +998.")
    if not 10 <= len(digits) <= 15:
        raise ToolInputError("phone must look like +998 90 123 45 67.")
    return "+" + digits


async def _free(ctx: ToolContext, service: Service, day: date, days: int) -> list[datetime]:
    tz = ctx.clinic.tz
    window_start = datetime.combine(day, time.min, tz)
    window_end = datetime.combine(day + timedelta(days=days), time.min, tz)
    busy = await ctx.bookings.busy_intervals(service.resource, window_start, window_end)
    return free_slots(ctx.clinic, service, busy, ctx.clock(), day, days)


def _nearest(free: list[datetime], target: datetime) -> list[str]:
    closest = sorted(free, key=lambda slot: abs(slot - target))[:MAX_ALTERNATIVES]
    return [slot.strftime("%H:%M") for slot in sorted(closest)]


async def _notify(call: Awaitable[None]) -> None:
    """Admin notifications are best effort: a failure never undoes a booking."""
    try:
        await call
    except Exception:
        logger.exception("Admin notification failed")


def _service_info(clinic: Clinic, service: Service) -> dict:
    resource = clinic.resources[service.resource]
    info = {
        "id": service.id,
        "name": service.name.model_dump(),
        "specialist": resource.name.model_dump(),
        "duration_minutes": service.duration_minutes,
        "price_uzs": service.price_uzs,
    }
    if service.price_note:
        info["price_note"] = service.price_note
    if resource.days is not None:
        order = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        info["specialist_days"] = [
            WEEKDAY_NAMES[order.index(day)] for day in order if day in resource.days
        ]
    return info


def _booking_info(clinic: Clinic, booking: BookingRecord) -> dict:
    service = clinic.service(booking.service_id)
    start = booking.slot_start.astimezone(clinic.tz)
    end = booking.slot_end.astimezone(clinic.tz)
    return {
        "booking_id": booking.id,
        "service_id": booking.service_id,
        "service_name": service.name.model_dump() if service else booking.service_id,
        "date": start.date().isoformat(),
        "weekday": WEEKDAY_NAMES[start.weekday()],
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M"),
        "patient_name": booking.patient_name,
        "phone": booking.phone,
        "price_uzs": service.price_uzs if service else None,
        "address": clinic.address,
    }


def _service(tool_input: dict, ctx: ToolContext) -> Service:
    service_id = _enum(tool_input, "service_id", [s.id for s in ctx.clinic.services])
    return ctx.clinic.service(service_id)


def _str(tool_input: dict, key: str) -> str:
    value = tool_input.get(key)
    if not isinstance(value, str):
        raise ToolInputError(f"{key} must be a string.")
    return value


def _int(tool_input: dict, key: str) -> int:
    value = tool_input.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError(f"{key} must be an integer.")
    return value


def _enum(tool_input: dict, key: str, allowed) -> str:
    # Strict mode doesn't guarantee the capitalisation of enum values.
    value = _str(tool_input, key).strip()
    for option in allowed:
        if option.lower() == value.lower():
            return option
    raise ToolInputError(f"{key} must be one of: {', '.join(allowed)}.")


def _date(tool_input: dict, key: str) -> date:
    try:
        return date.fromisoformat(_str(tool_input, key))
    except ValueError:
        raise ToolInputError(f"{key} must be a date in YYYY-MM-DD form.") from None


def _time(tool_input: dict, key: str) -> time:
    value = _str(tool_input, key)
    if not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ToolInputError(f"{key} must be HH:MM.")
    try:
        return time.fromisoformat(value)
    except ValueError:
        raise ToolInputError(f"{key} must be a valid time of day, HH:MM.") from None


def _patient_name(tool_input: dict) -> str:
    name = " ".join(_str(tool_input, "patient_name").split())
    if not 2 <= len(name) <= 100 or not any(c.isalpha() for c in name):
        raise ToolInputError("patient_name must be the patient's name, 2-100 characters.")
    return name


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _ok(payload: dict) -> ToolResult:
    return ToolResult(content=_json(payload))


def _error(message: str, **details) -> ToolResult:
    return ToolResult(content=_json({"error": message, **details}), is_error=True)
