"""In-memory implementations of the core ports, and a scripted Claude client."""

import asyncio
import copy
import json
from dataclasses import asdict, replace
from datetime import datetime

from anthropic.types import Message

from receptionist.core.agent import context_tokens
from receptionist.core.ports import BookingRecord, NewBooking, SessionState, SlotTaken

# --- Claude ------------------------------------------------------------------


def thinking(signature: str = "sig-1") -> dict:
    # Claude Opus 5.5 returns thinking text empty by default; the signature matters.
    return {"type": "thinking", "thinking": "", "signature": signature}


def text(value: str) -> dict:
    return {"type": "text", "text": value}


def tool_use(block_id: str, name: str, tool_input: dict) -> dict:
    return {"type": "tool_use", "id": block_id, "name": name, "input": tool_input}


def message(
    *blocks: dict,
    stop_reason: str = "end_turn",
    input_tokens: int = 1000,
    output_tokens: int = 50,
    cache_read: int = 0,
    model: str = "claude-opus-5-5",
) -> Message:
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": list(blocks),
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": 0,
            },
        }
    )


class FakeClaude:
    """Returns scripted responses (or raises scripted errors) and records requests."""

    def __init__(self, *responses, delay: float = 0.0) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.delay = delay
        self.messages = self  # client.messages.create(...)

    async def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.responses:
            raise AssertionError("FakeClaude ran out of scripted responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# --- conversation store ------------------------------------------------------


class FakeConversationStore:
    """Keeps content as JSON text, like the database, so tests see a real round trip."""

    def __init__(self) -> None:
        self.sessions: dict[int, dict] = {}

    async def current_session(self, chat_id: int) -> SessionState | None:
        for session_id, s in self.sessions.items():
            if s["chat_id"] == chat_id and s["ended_at"] is None:
                rows = s["rows"]
                last_assistant = next((r for r in reversed(rows) if r[0] == "assistant"), None)
                return SessionState(
                    id=session_id,
                    fingerprint=s["fingerprint"],
                    messages=[{"role": role, "content": json.loads(c)} for role, c, _ in rows],
                    last_activity=s["last_activity"],
                    context_tokens=context_tokens(last_assistant[2] if last_assistant else None),
                )
        return None

    async def start_session(self, chat_id: int, fingerprint: str, at: datetime) -> SessionState:
        session_id = len(self.sessions) + 1
        self.sessions[session_id] = {
            "chat_id": chat_id,
            "fingerprint": fingerprint,
            "rows": [],
            "ended_at": None,
            "summary": None,
            "last_activity": at,
        }
        return SessionState(session_id, fingerprint, [], at, 0)

    async def end_session(self, session_id: int, summary: str | None, at: datetime) -> None:
        s = self.sessions[session_id]
        if s["ended_at"] is None:
            s["ended_at"], s["summary"] = at, summary

    async def append(self, session_id, role, content, usage, at: datetime) -> None:
        s = self.sessions[session_id]
        s["rows"].append((role, json.dumps(content, ensure_ascii=False), usage))
        s["last_activity"] = at

    def history(self, session_id: int) -> list[dict]:
        rows = self.sessions[session_id]["rows"]
        return [{"role": role, "content": json.loads(c)} for role, c, _ in rows]


# --- bookings, handoffs, notifications ----------------------------------------


class FakeBookings:
    def __init__(self) -> None:
        self.rows: list[BookingRecord] = []
        self.fail_next_create = False  # simulate losing a race to another request

    async def busy_intervals(self, resource: str, start: datetime, end: datetime):
        return [
            (b.slot_start, b.slot_end)
            for b in self.rows
            if b.resource == resource
            and b.status == "confirmed"
            and b.slot_start < end
            and b.slot_end > start
        ]

    async def create(self, booking: NewBooking) -> BookingRecord:
        overlaps = await self.busy_intervals(booking.resource, booking.slot_start, booking.slot_end)
        if overlaps or self.fail_next_create:
            self.fail_next_create = False
            raise SlotTaken
        record = BookingRecord(id=len(self.rows) + 1, status="confirmed", **asdict(booking))
        self.rows.append(record)
        return record

    async def upcoming_for_chat(self, chat_id: int, now: datetime) -> list[BookingRecord]:
        mine = [
            b
            for b in self.rows
            if b.chat_id == chat_id and b.status == "confirmed" and b.slot_start > now
        ]
        return sorted(mine, key=lambda b: b.slot_start)

    async def cancel(self, chat_id: int, booking_id: int, now: datetime):
        for i, b in enumerate(self.rows):
            if b.id == booking_id and b in await self.upcoming_for_chat(chat_id, now):
                self.rows[i] = replace(b, status="cancelled")
                return self.rows[i]
        return None


class FakeHandoffs:
    def __init__(self) -> None:
        self.opened: list[tuple[int, str, str]] = []

    async def open(self, chat_id: int, reason: str, summary: str) -> int:
        self.opened.append((chat_id, reason, summary))
        return len(self.opened)


class RecordingNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.events: list[tuple] = []
        self.fail = fail

    async def _record(self, *event) -> None:
        self.events.append(event)
        if self.fail:
            raise RuntimeError("Telegram is down")

    async def booking_created(self, booking) -> None:
        await self._record("created", booking.id)

    async def booking_cancelled(self, booking) -> None:
        await self._record("cancelled", booking.id)

    async def handoff_requested(self, chat_id, handoff_id, reason, summary) -> None:
        await self._record("handoff", chat_id, handoff_id, reason)
