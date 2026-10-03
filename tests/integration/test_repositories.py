"""Repositories and tools against PostgreSQL, including concurrent bookings."""

import asyncio
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from receptionist.core.clinic import load_clinic
from receptionist.core.ports import NewBooking, SlotTaken
from receptionist.core.tools import ToolContext, run_tool
from receptionist.db.repositories import SqlBookingRepository, SqlHandoffRepository
from tests.fakes import RecordingNotifier

CLINIC = load_clinic(Path(__file__).resolve().parents[2] / "data" / "clinic.yaml")
TUE = date(2026, 10, 6)
NOW = datetime.combine(date(2026, 10, 5), time(8), CLINIC.tz)


def at(hour: int, minute: int = 0, day: date = TUE) -> datetime:
    return datetime.combine(day, time(hour, minute), CLINIC.tz)


@pytest.fixture
def chats(sql) -> tuple[int, int]:
    rows = sql("INSERT INTO chats (tg_chat_id) VALUES (101), (102) RETURNING id")
    return rows[0][0], rows[1][0]


@pytest.fixture
async def sessions(database_url):
    engine = create_async_engine(database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def repo(sessions) -> SqlBookingRepository:
    return SqlBookingRepository(sessions)


def new_booking(chat: int, start: datetime, minutes: int = 60, resource="therapist"):
    return NewBooking(
        chat_id=chat,
        service_id="filling",
        resource=resource,
        slot_start=start,
        slot_end=start + timedelta(minutes=minutes),
        patient_name="Aziz",
        phone="+998901234567",
    )


async def test_create_list_and_cancel(repo, chats):
    chat, other = chats
    first = await repo.create(new_booking(chat, at(10)))
    await repo.create(new_booking(other, at(12)))
    assert first.id and first.status == "confirmed"
    assert first.slot_start == at(10)

    assert await repo.busy_intervals("therapist", at(0), at(23)) == [
        (at(10), at(11)),
        (at(12), at(13)),
    ]
    assert await repo.busy_intervals("therapist", at(11), at(12)) == []  # touching only
    assert await repo.busy_intervals("hygienist", at(0), at(23)) == []

    assert [b.id for b in await repo.upcoming_for_chat(chat, NOW)] == [first.id]
    assert await repo.cancel(other, first.id, NOW) is None  # not their booking
    cancelled = await repo.cancel(chat, first.id, NOW)
    assert cancelled.status == "cancelled" and cancelled.id == first.id
    assert await repo.cancel(chat, first.id, NOW) is None  # already cancelled
    assert await repo.upcoming_for_chat(chat, NOW) == []
    assert await repo.busy_intervals("therapist", at(10), at(11)) == []


async def test_past_bookings_are_not_upcoming_or_cancellable(repo, chats):
    chat, _ = chats
    booking = await repo.create(new_booking(chat, at(10)))
    later = at(12)
    assert await repo.upcoming_for_chat(chat, later) == []
    assert await repo.cancel(chat, booking.id, later) is None


async def test_overlap_raises_slot_taken(repo, chats):
    chat, other = chats
    await repo.create(new_booking(chat, at(10)))
    with pytest.raises(SlotTaken):
        await repo.create(new_booking(other, at(10, 30)))


async def test_concurrent_bookings_of_one_slot_let_exactly_one_through(repo, chats):
    chat, other = chats
    results = await asyncio.gather(
        repo.create(new_booking(chat, at(10))),
        repo.create(new_booking(other, at(10))),
        return_exceptions=True,
    )
    assert sorted(type(r).__name__ for r in results) == ["BookingRecord", "SlotTaken"]


async def test_double_booking_race_through_the_tool(sessions, chats, sql):
    """Two patients confirm the same free slot at the same moment."""
    notifier = RecordingNotifier()

    def context(chat: int) -> ToolContext:
        return ToolContext(
            chat,
            CLINIC,
            SqlBookingRepository(sessions),
            SqlHandoffRepository(sessions),
            notifier,
            clock=lambda: NOW,
        )

    tool_input = {
        "service_id": "filling",
        "date": TUE.isoformat(),
        "time": "10:00",
        "patient_name": "Aziz",
        "phone": "901234567",
        "confirmed": True,
    }
    results = await asyncio.gather(
        *(run_tool("create_booking", tool_input, context(c)) for c in chats)
    )
    assert sorted(r.is_error for r in results) == [False, True]
    loser = json.loads(next(r for r in results if r.is_error).content)
    assert "moment ago" in loser["error"] or "not available" in loser["error"]
    assert [e[0] for e in notifier.events] == ["created"]
    async with sessions() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM bookings WHERE status = 'confirmed'")
        )
    assert count == 1


async def test_handoff_switches_the_chat_to_operator_mode(sessions, chats):
    chat, other = chats
    handoffs = SqlHandoffRepository(sessions)
    handoff_id = await handoffs.open(chat, "complaint", "Unhappy with a filling.")
    async with sessions() as session:
        modes = dict((await session.execute(text("SELECT id, mode FROM chats"))).all())
        row = (
            await session.execute(
                text("SELECT chat_id, reason, summary, closed_at FROM handoffs WHERE id = :id"),
                {"id": handoff_id},
            )
        ).one()
    assert modes == {chat: "operator", other: "bot"}
    assert tuple(row) == (chat, "complaint", "Unhappy with a filling.", None)
