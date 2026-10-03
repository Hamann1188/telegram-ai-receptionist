"""How SqlBookingRepository.create maps database errors, without a database."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from receptionist.core.ports import NewBooking, SlotTaken
from receptionist.db.repositories import MAX_INSERT_ATTEMPTS, SqlBookingRepository

START = datetime(2026, 10, 6, 5, tzinfo=UTC)
BOOKING = NewBooking(1, "filling", "therapist", START, START + timedelta(hours=1), "A", "+998")


def db_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("INSERT ...", {}, SimpleNamespace(sqlstate=sqlstate))


class ScriptedSessions:
    """A session factory whose commits fail with the scripted errors, then succeed."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.commits = 0

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def add(self, row) -> None:
        self.row = row

    async def commit(self) -> None:
        self.commits += 1
        if self.errors:
            raise self.errors.pop(0)
        self.row.id = 42


async def test_deadlock_is_retried_and_ends_as_slot_taken():
    sessions = ScriptedSessions(db_error("40P01"), db_error("23P01"))
    with pytest.raises(SlotTaken):
        await SqlBookingRepository(sessions).create(BOOKING)
    assert sessions.commits == 2


async def test_deadlock_then_success_returns_the_booking():
    sessions = ScriptedSessions(db_error("40P01"))
    record = await SqlBookingRepository(sessions).create(BOOKING)
    assert record.id == 42 and record.status == "confirmed"


async def test_repeated_deadlocks_give_up():
    errors = [db_error("40P01") for _ in range(MAX_INSERT_ATTEMPTS)]
    sessions = ScriptedSessions(*errors)
    with pytest.raises(DBAPIError):
        await SqlBookingRepository(sessions).create(BOOKING)
    assert sessions.commits == MAX_INSERT_ATTEMPTS


async def test_other_database_errors_are_not_retried():
    sessions = ScriptedSessions(db_error("23503"))  # foreign key violation: unknown chat
    with pytest.raises(DBAPIError):
        await SqlBookingRepository(sessions).create(BOOKING)
    assert sessions.commits == 1
