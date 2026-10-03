"""In-memory implementations of the core ports for unit tests."""

from dataclasses import asdict, replace
from datetime import datetime

from receptionist.core.ports import BookingRecord, NewBooking, SlotTaken


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
