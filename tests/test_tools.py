import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from receptionist.core.clinic import load_clinic
from receptionist.core.ports import NewBooking
from receptionist.core.tools import (
    HANDLERS,
    MAX_SEARCH_DAYS,
    ToolContext,
    normalize_phone,
    run_tool,
    tool_definitions,
)
from tests.fakes import FakeBookings, FakeHandoffs, RecordingNotifier

CLINIC = load_clinic(Path(__file__).resolve().parents[1] / "data" / "clinic.yaml")
MON, TUE = date(2026, 10, 5), date(2026, 10, 6)
NOW = datetime.combine(MON, time(8, 0), CLINIC.tz)  # Monday 08:00 in Tashkent
CHAT, OTHER_CHAT = 1, 2

UNSUPPORTED_KEYWORDS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "maxItems",
    "uniqueItems",
}
SUPPORTED_FORMATS = {
    "date-time",
    "time",
    "date",
    "duration",
    "email",
    "hostname",
    "uri",
    "ipv4",
    "ipv6",
    "uuid",
}


@pytest.fixture
def bookings() -> FakeBookings:
    return FakeBookings()


@pytest.fixture
def handoffs() -> FakeHandoffs:
    return FakeHandoffs()


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def ctx(bookings, handoffs, notifier) -> ToolContext:
    return ToolContext(CHAT, CLINIC, bookings, handoffs, notifier, clock=lambda: NOW)


async def call(ctx, name, **tool_input):
    result = await run_tool(name, tool_input, ctx)
    return result, json.loads(result.content)


def booking_input(**overrides) -> dict:
    values = {
        "service_id": "filling",
        "date": TUE.isoformat(),
        "time": "10:00",
        "patient_name": "Aziz Karimov",
        "phone": "+998 90 123 45 67",
        "confirmed": True,
    }
    return values | overrides


def local(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), CLINIC.tz)


async def seed(bookings, start: datetime, chat=CHAT, service_id="filling"):
    service = CLINIC.service(service_id)
    return await bookings.create(
        NewBooking(
            chat_id=chat,
            service_id=service.id,
            resource=service.resource,
            slot_start=start,
            slot_end=start + timedelta(minutes=service.duration_minutes),
            patient_name="Someone",
            phone="+998901111111",
        )
    )


# --- schemas -----------------------------------------------------------------


def _walk(schema: dict, path: str = "input_schema"):
    yield path, schema
    for name, child in schema.get("properties", {}).items():
        yield from _walk(child, f"{path}.{name}")
    if isinstance(schema.get("items"), dict):
        yield from _walk(schema["items"], f"{path}[]")


def test_tool_schemas_follow_strict_mode_rules():
    tools = tool_definitions(CLINIC)
    assert len(tools) <= 20  # strict tools per request
    assert len({t["name"] for t in tools}) == len(tools)
    assert {t["name"] for t in tools} == set(HANDLERS)
    for tool in tools:
        assert tool["strict"] is True
        assert len(tool["description"]) > 40, tool["name"]
        for path, node in _walk(tool["input_schema"]):
            assert not UNSUPPORTED_KEYWORDS & node.keys(), f"{tool['name']} {path}"
            if node.get("type") == "object":
                assert node["additionalProperties"] is False, path
                # No optional parameters: they count toward a per-request limit.
                assert node["required"] == list(node["properties"]), path
            if "format" in node:
                assert node["format"] in SUPPORTED_FORMATS, path
            if node is not tool["input_schema"]:
                assert node.get("description"), f"{tool['name']} {path} needs a description"


def test_definitions_are_stable_and_follow_the_clinic_profile():
    first, second = tool_definitions(CLINIC), tool_definitions(CLINIC)
    assert json.dumps(first) == json.dumps(second)  # byte-identical for the prompt cache
    by_name = {t["name"]: t["input_schema"]["properties"] for t in first}
    assert by_name["find_free_slots"]["service_id"]["enum"] == [s.id for s in CLINIC.services]
    assert by_name["get_clinic_info"]["topic"]["enum"] == sorted(CLINIC.faq)


# --- information tools ---------------------------------------------------------


async def test_get_clinic_info_returns_the_official_answer(ctx):
    result, body = await call(ctx, "get_clinic_info", topic="Parking")  # case-insensitive
    assert not result.is_error
    assert body == {"topic": "parking", "answer": CLINIC.faq["parking"]}


async def test_get_clinic_info_rejects_unknown_topic(ctx):
    result, body = await call(ctx, "get_clinic_info", topic="pizza")
    assert result.is_error and "topic must be one of" in body["error"]


async def test_list_services_has_prices_and_specialist_days(ctx):
    result, body = await call(ctx, "list_services")
    services = {s["id"]: s for s in body["services"]}
    assert not result.is_error and body["currency"] == "UZS"
    assert services["hygiene"]["price_uzs"] == 450_000
    assert services["child-checkup"]["specialist_days"] == ["Tuesday", "Thursday", "Saturday"]
    assert "specialist_days" not in services["filling"]
    assert services["filling"]["name"]["ru"].startswith("Лечение кариеса")


# --- find_free_slots -------------------------------------------------------------


async def test_find_free_slots_groups_times_by_day(ctx):
    result, body = await call(
        ctx, "find_free_slots", service_id="filling", date_from=TUE.isoformat(), days=1
    )
    assert not result.is_error
    (day,) = body["days"]
    assert (day["date"], day["weekday"]) == ("2026-10-06", "Tuesday")
    assert day["times"][:2] == ["09:00", "09:30"] and day["times"][-1] == "19:00"
    assert body["timezone"] == "Asia/Tashkent"


async def test_find_free_slots_skips_booked_times(ctx, bookings):
    await seed(bookings, local(TUE, 10), chat=OTHER_CHAT)
    _, body = await call(
        ctx, "find_free_slots", service_id="filling", date_from=TUE.isoformat(), days=1
    )
    times = body["days"][0]["times"]
    assert "09:30" not in times and "10:00" not in times and "10:30" not in times
    assert "09:00" in times and "11:00" in times


async def test_find_free_slots_from_a_past_date_starts_today(ctx):
    _, body = await call(
        ctx, "find_free_slots", service_id="filling", date_from="2026-09-01", days=1
    )
    (day,) = body["days"]
    assert day["date"] == MON.isoformat()
    assert day["times"][0] == "09:00"  # 08:00 now + 60 min notice


@pytest.mark.parametrize("days", [0, -1, MAX_SEARCH_DAYS + 1])
async def test_find_free_slots_rejects_bad_day_counts(ctx, days):
    result, body = await call(
        ctx, "find_free_slots", service_id="filling", date_from=TUE.isoformat(), days=days
    )
    assert result.is_error and "between 1 and 14" in body["error"]


async def test_find_free_slots_beyond_the_horizon_explains_why(ctx):
    _, body = await call(
        ctx, "find_free_slots", service_id="filling", date_from="2027-03-01", days=3
    )
    assert body["days"] == [] and "last bookable date is 2026-11-04" in body["note"]


async def test_find_free_slots_caps_long_results(ctx):
    _, body = await call(
        ctx, "find_free_slots", service_id="consultation", date_from=TUE.isoformat(), days=14
    )
    assert sum(len(d["times"]) for d in body["days"]) == 60
    assert "later date" in body["note"]


async def test_find_free_slots_for_childrens_dentist_only_on_working_days(ctx):
    _, body = await call(
        ctx, "find_free_slots", service_id="child-checkup", date_from=MON.isoformat(), days=7
    )
    assert [d["weekday"] for d in body["days"]] == ["Tuesday", "Thursday", "Saturday"]


async def test_find_free_slots_rejects_malformed_date(ctx):
    result, body = await call(
        ctx, "find_free_slots", service_id="filling", date_from="06.10.2026", days=1
    )
    assert result.is_error and "YYYY-MM-DD" in body["error"]


# --- create_booking ------------------------------------------------------------


async def test_unconfirmed_booking_is_never_created(ctx, bookings, notifier):
    result, body = await call(ctx, "create_booking", **booking_input(confirmed=False))
    assert result.is_error
    assert "confirmed=true" in body["error"] and "the time is free" in body["error"]
    assert bookings.rows == [] and notifier.events == []


async def test_confirmed_booking_is_created_and_admins_notified(ctx, bookings, notifier):
    result, body = await call(ctx, "create_booking", **booking_input())
    assert not result.is_error
    booking = body["booking"]
    assert (booking["date"], booking["start"], booking["end"]) == ("2026-10-06", "10:00", "11:00")
    assert booking["phone"] == "+998901234567" and booking["price_uzs"] == 550_000
    (row,) = bookings.rows
    assert row.slot_start == local(TUE, 10) and row.chat_id == CHAT
    assert row.resource == "therapist"
    assert notifier.events == [("created", row.id)]
    assert CLINIC.faq["cancellation"] in body["remind_patient"]


async def test_booking_a_taken_slot_fails_even_when_confirmed(ctx, bookings):
    await seed(bookings, local(TUE, 10), chat=OTHER_CHAT)
    result, body = await call(ctx, "create_booking", **booking_input(time="10:30"))
    assert result.is_error and "not available" in body["error"]
    assert "11:00" in body["free_times_that_day"]
    assert len(bookings.rows) == 1


async def test_unconfirmed_check_reports_an_unavailable_slot_first(ctx, bookings):
    await seed(bookings, local(TUE, 10), chat=OTHER_CHAT)
    result, body = await call(ctx, "create_booking", **booking_input(confirmed=False))
    assert result.is_error and "not available" in body["error"]


async def test_losing_a_race_returns_fresh_alternatives(ctx, bookings, notifier):
    bookings.fail_next_create = True
    result, body = await call(ctx, "create_booking", **booking_input())
    assert result.is_error and "a moment ago" in body["error"]
    assert body["free_times_that_day"]
    assert bookings.rows == [] and notifier.events == []


@pytest.mark.parametrize(
    "time_value",
    ["07:00", "09:15", "19:30", "20:00"],  # before opening, not on the grid, ends too late
)
async def test_times_outside_the_schedule_are_not_available(ctx, time_value):
    result, body = await call(ctx, "create_booking", **booking_input(time=time_value))
    assert result.is_error and "not available" in body["error"]


@pytest.mark.parametrize("time_value", ["25:00", "9:00", "10-00"])
async def test_malformed_times_are_rejected(ctx, time_value):
    result, _ = await call(ctx, "create_booking", **booking_input(time=time_value))
    assert result.is_error


async def test_closed_day_is_not_available(ctx):
    result, body = await call(ctx, "create_booking", **booking_input(date="2026-10-11"))
    assert result.is_error and body["free_times_that_day"] == []


async def test_too_soon_is_not_available(ctx):
    result, _ = await call(
        ctx, "create_booking", **booking_input(date=MON.isoformat(), time="08:30")
    )
    assert result.is_error


@pytest.mark.parametrize("name", ["", "A", "12345", "x" * 101])
async def test_invalid_names_are_rejected(ctx, name):
    result, body = await call(ctx, "create_booking", **booking_input(patient_name=name))
    assert result.is_error and "patient_name" in body["error"]


async def test_name_whitespace_is_normalized(ctx, bookings):
    await call(ctx, "create_booking", **booking_input(patient_name="  Aziz   Karimov "))
    assert bookings.rows[0].patient_name == "Aziz Karimov"


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("+998 90 123 45 67", "+998901234567"),
        ("+998 (90) 123-45-67", "+998901234567"),
        ("998901234567", "+998901234567"),
        ("90 123 45 67", "+998901234567"),
        ("+44 20 7946 0958", "+442079460958"),
        ("+7 701 234 5678", "+77012345678"),
    ],
)
def test_phone_normalization(raw, normalized):
    assert normalize_phone(raw) == normalized


@pytest.mark.parametrize("raw", ["12345", "phone", "+998 90 123 45", "+998 90 123 45 678", ""])
async def test_invalid_phones_are_rejected(ctx, raw):
    result, body = await call(ctx, "create_booking", **booking_input(phone=raw))
    assert result.is_error and "phone" in body["error"].lower()


async def test_upcoming_booking_limit_per_chat(ctx, bookings):
    for hour in (9, 11, 13):
        await seed(bookings, local(TUE, hour))
    result, body = await call(ctx, "create_booking", **booking_input(time="15:00"))
    assert result.is_error and "already has 3 upcoming" in body["error"]


async def test_notifier_failure_does_not_undo_the_booking(bookings, handoffs):
    ctx = ToolContext(
        CHAT, CLINIC, bookings, handoffs, RecordingNotifier(fail=True), clock=lambda: NOW
    )
    result, body = await call(ctx, "create_booking", **booking_input())
    assert not result.is_error and body["booked"] is True
    assert len(bookings.rows) == 1


# --- list and cancel -----------------------------------------------------------


async def test_list_my_bookings_shows_only_this_chats_upcoming(ctx, bookings):
    mine = await seed(bookings, local(TUE, 12))
    await seed(bookings, local(TUE, 14), chat=OTHER_CHAT)
    await seed(bookings, local(MON, 6, 30))  # already in the past at 08:00
    _, body = await call(ctx, "list_my_bookings")
    assert [b["booking_id"] for b in body["bookings"]] == [mine.id]


async def test_cancel_own_booking(ctx, bookings, notifier):
    booking = await seed(bookings, local(date(2026, 10, 8), 12))
    result, body = await call(ctx, "cancel_booking", booking_id=booking.id)
    assert not result.is_error and body["cancelled"] is True
    assert "late_cancellation" not in body
    assert bookings.rows[0].status == "cancelled"
    assert notifier.events == [("cancelled", booking.id)]


async def test_late_cancellation_mentions_the_fee(ctx, bookings):
    booking = await seed(bookings, local(MON, 18))  # 10 hours from now
    _, body = await call(ctx, "cancel_booking", booking_id=booking.id)
    assert body["late_cancellation"]["fee_uzs"] == 50_000


async def test_cannot_cancel_another_chats_booking(ctx, bookings, notifier):
    booking = await seed(bookings, local(TUE, 12), chat=OTHER_CHAT)
    result, body = await call(ctx, "cancel_booking", booking_id=booking.id)
    assert result.is_error and "list_my_bookings" in body["error"]
    assert bookings.rows[0].status == "confirmed" and notifier.events == []


# --- handoff -------------------------------------------------------------------


async def test_handoff_opens_a_handoff_and_notifies_admins(ctx, handoffs, notifier):
    result, body = await call(
        ctx, "handoff_to_human", reason="complaint", summary="Patient unhappy with a filling."
    )
    assert result.handoff is True and not result.is_error
    assert body["handed_off"] is True
    assert handoffs.opened == [(CHAT, "complaint", "Patient unhappy with a filling.")]
    assert notifier.events == [("handoff", CHAT, 1, "complaint")]


async def test_handoff_needs_a_summary(ctx, handoffs):
    result, _ = await call(ctx, "handoff_to_human", reason="other", summary="   ")
    assert result.is_error and result.handoff is False and handoffs.opened == []


# --- dispatch ------------------------------------------------------------------


async def test_unknown_tool(ctx):
    result, body = await call(ctx, "launch_rocket")
    assert result.is_error and "Unknown tool" in body["error"]


async def test_non_object_input(ctx):
    result = await run_tool("list_services", ["not", "an", "object"], ctx)
    assert result.is_error


async def test_missing_field(ctx):
    result, body = await call(ctx, "find_free_slots", service_id="filling", days=1)
    assert result.is_error and "date_from" in body["error"]


async def test_boolean_is_not_an_integer(ctx):
    result, _ = await call(ctx, "cancel_booking", booking_id=True)
    assert result.is_error


async def test_repository_failure_becomes_a_generic_error(ctx, bookings, caplog):
    async def broken(*args, **kwargs):
        raise ConnectionError("database is down")

    bookings.upcoming_for_chat = broken
    result, body = await call(ctx, "list_my_bookings")
    assert result.is_error and "Internal error" in body["error"]
    assert "database is down" not in body["error"]
    assert "Tool list_my_bookings failed" in caplog.text
