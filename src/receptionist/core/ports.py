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
