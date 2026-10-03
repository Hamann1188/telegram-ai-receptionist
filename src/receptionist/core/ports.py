"""Interfaces between the assistant core and the outside world.

`core/` depends only on these; `db/` and `telegram/` implement them.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from receptionist.core.slots import Interval


class SlotTaken(Exception):
    """Another booking took the slot first."""


@dataclass(frozen=True)
class NewBooking:
    chat_id: int
    service_id: str
    resource: str
    slot_start: datetime
    slot_end: datetime
    patient_name: str
    phone: str


@dataclass(frozen=True)
class BookingRecord:
    id: int
    chat_id: int
    service_id: str
    resource: str
    slot_start: datetime
    slot_end: datetime
    patient_name: str
    phone: str
    status: str


class BookingRepository(Protocol):
    async def busy_intervals(self, resource: str, start: datetime, end: datetime) -> list[Interval]:
        """Confirmed bookings of `resource` that overlap [start, end)."""
        ...

    async def create(self, booking: NewBooking) -> BookingRecord:
        """Insert a confirmed booking. Raises SlotTaken if it overlaps another one."""
        ...

    async def upcoming_for_chat(self, chat_id: int, now: datetime) -> list[BookingRecord]:
        """Confirmed bookings of the chat that start after `now`, earliest first."""
        ...

    async def cancel(self, chat_id: int, booking_id: int, now: datetime) -> BookingRecord | None:
        """Cancel the chat's own upcoming booking; None if there is no such booking."""
        ...


@dataclass
class SessionState:
    """An open conversation with Claude, as stored."""

    id: int
    fingerprint: str | None
    messages: list[dict]  # [{"role": ..., "content": [blocks]}], in order, verbatim
    last_activity: datetime
    context_tokens: int  # prompt + output tokens of the last Claude call, 0 if none


class ConversationStore(Protocol):
    async def current_session(self, chat_id: int) -> SessionState | None:
        """The chat's open session with its messages, or None."""
        ...

    # Timestamps come from the agent's clock, not the database's, so session timeouts
    # and the context's "current time" use one time source.

    async def start_session(self, chat_id: int, fingerprint: str, at: datetime) -> SessionState: ...

    async def end_session(self, session_id: int, summary: str | None, at: datetime) -> None: ...

    async def append(
        self, session_id: int, role: str, content: list[dict], usage: dict | None, at: datetime
    ) -> None:
        """Append one message. Stored content must read back byte-identical."""
        ...


class HandoffRepository(Protocol):
    async def open(self, chat_id: int, reason: str, summary: str) -> int:
        """Switch the chat to operator mode and record the handoff; returns its id."""
        ...


class Notifier(Protocol):
    """Messages to the clinic's admin group."""

    async def booking_created(self, booking: BookingRecord) -> None: ...

    async def booking_cancelled(self, booking: BookingRecord) -> None: ...

    async def handoff_requested(
        self, chat_id: int, handoff_id: int, reason: str, summary: str
    ) -> None: ...
