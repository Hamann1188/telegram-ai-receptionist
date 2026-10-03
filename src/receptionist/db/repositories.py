"""PostgreSQL implementations of the core ports."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from receptionist.core.ports import BookingRecord, NewBooking, SlotTaken
from receptionist.core.slots import Interval
from receptionist.db.models import Booking, Chat, Handoff

EXCLUSION_VIOLATION = "23P01"  # SQLSTATE of ex_bookings_no_overlap

Sessions = async_sessionmaker[AsyncSession]


def _record(row: Booking) -> BookingRecord:
    return BookingRecord(
        id=row.id,
        chat_id=row.chat_id,
        service_id=row.service_id,
        resource=row.resource,
        slot_start=row.slot_start,
        slot_end=row.slot_end,
        patient_name=row.patient_name,
        phone=row.phone,
        status=row.status,
    )


class SqlBookingRepository:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def busy_intervals(self, resource: str, start: datetime, end: datetime) -> list[Interval]:
        stmt = (
            select(Booking.slot_start, Booking.slot_end)
            .where(
                Booking.resource == resource,
                Booking.status == "confirmed",
                Booking.slot_start < end,
                Booking.slot_end > start,
            )
            .order_by(Booking.slot_start)
        )
        async with self._sessions() as session:
            return [(row.slot_start, row.slot_end) for row in await session.execute(stmt)]

    async def create(self, booking: NewBooking) -> BookingRecord:
        row = Booking(
            chat_id=booking.chat_id,
            service_id=booking.service_id,
            resource=booking.resource,
            slot_start=booking.slot_start,
            slot_end=booking.slot_end,
            patient_name=booking.patient_name,
            phone=booking.phone,
            status="confirmed",
        )
        async with self._sessions() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                # The exclusion constraint is the real guard against double booking:
                # it holds even when two requests pass the free-slot check together.
                if getattr(exc.orig, "sqlstate", None) == EXCLUSION_VIOLATION:
                    raise SlotTaken from exc
                raise
        return _record(row)

    async def upcoming_for_chat(self, chat_id: int, now: datetime) -> list[BookingRecord]:
        stmt = (
            select(Booking)
            .where(
                Booking.chat_id == chat_id,
                Booking.status == "confirmed",
                Booking.slot_start > now,
            )
            .order_by(Booking.slot_start)
        )
        async with self._sessions() as session:
            return [_record(row) for row in await session.scalars(stmt)]

    async def cancel(self, chat_id: int, booking_id: int, now: datetime) -> BookingRecord | None:
        stmt = (
            update(Booking)
            .where(
                Booking.id == booking_id,
                Booking.chat_id == chat_id,
                Booking.status == "confirmed",
                Booking.slot_start > now,
            )
            .values(status="cancelled", cancelled_at=now)
            .returning(Booking)
            .execution_options(synchronize_session=False)
        )
        async with self._sessions.begin() as session:
            row = (await session.scalars(stmt)).one_or_none()
            return _record(row) if row is not None else None


class SqlHandoffRepository:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def open(self, chat_id: int, reason: str, summary: str) -> int:
        async with self._sessions.begin() as session:
            await session.execute(update(Chat).where(Chat.id == chat_id).values(mode="operator"))
            handoff = Handoff(chat_id=chat_id, reason=reason, summary=summary)
            session.add(handoff)
            await session.flush()
            return handoff.id
