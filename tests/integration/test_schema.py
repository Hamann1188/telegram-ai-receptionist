import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from receptionist.db.models import Base
from tests.integration.conftest import alembic_config

TASHKENT = ZoneInfo("Asia/Tashkent")
BOOKING = (
    "INSERT INTO bookings (chat_id, service_id, resource, slot_start, slot_end, "
    "patient_name, phone, status) VALUES (:chat, :service, :resource, :start, :end, "
    "'Aziz', '+998901234567', :status)"
)


@pytest.fixture
def chat(sql) -> int:
    return sql("INSERT INTO chats (tg_chat_id, language) VALUES (1001, 'ru') RETURNING id")[0][0]


def book(sql, chat, start, end, resource="therapist", status="confirmed", service="filling"):
    sql(
        BOOKING,
        chat=chat,
        service=service,
        resource=resource,
        start=_at(start),
        end=_at(end),
        status=status,
    )


def _at(hh_mm: str) -> datetime:
    hour, minute = map(int, hh_mm.split(":"))
    return datetime(2026, 10, 5, hour, minute, tzinfo=TASHKENT)


def test_models_match_the_migrated_schema(database_url):
    async def diff():
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(
                    lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
                )
        finally:
            await engine.dispose()

    assert asyncio.run(diff()) == []


def test_migrations_downgrade_and_upgrade_again(database_url, sql):
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    assert not sql("SELECT 1 FROM information_schema.tables WHERE table_name = 'bookings'")
    command.upgrade(config, "head")
    assert sql("SELECT 1 FROM information_schema.tables WHERE table_name = 'bookings'")


def test_overlapping_confirmed_bookings_of_one_resource_are_rejected(sql, chat):
    book(sql, chat, "10:00", "11:00")
    with pytest.raises(IntegrityError, match="ex_bookings_no_overlap"):
        book(sql, chat, "10:30", "11:30")


def test_adjacent_bookings_are_allowed(sql, chat):
    book(sql, chat, "10:00", "11:00")
    book(sql, chat, "11:00", "12:00")
    book(sql, chat, "09:00", "10:00")
    assert len(sql("SELECT id FROM bookings")) == 3


def test_other_resources_and_cancelled_bookings_do_not_conflict(sql, chat):
    book(sql, chat, "10:00", "11:00")
    book(sql, chat, "10:00", "11:00", resource="hygienist", service="hygiene")
    book(sql, chat, "10:30", "11:00", status="cancelled")
    book(sql, chat, "11:00", "12:00", status="cancelled")
    book(sql, chat, "11:00", "12:00")  # a cancelled booking frees the slot


def test_booking_must_end_after_it_starts(sql, chat):
    with pytest.raises(IntegrityError, match="ck_bookings_slot_order"):
        book(sql, chat, "11:00", "10:00")


def test_a_chat_has_at_most_one_open_session(sql, chat):
    sql("INSERT INTO sessions (chat_id) VALUES (:chat)", chat=chat)
    with pytest.raises(IntegrityError, match="uq_sessions_one_open_per_chat"):
        sql("INSERT INTO sessions (chat_id) VALUES (:chat)", chat=chat)
    sql("UPDATE sessions SET ended_at = now() WHERE chat_id = :chat", chat=chat)
    sql("INSERT INTO sessions (chat_id) VALUES (:chat)", chat=chat)
    assert len(sql("SELECT id FROM sessions WHERE chat_id = :chat", chat=chat)) == 2


def test_message_ordinals_are_unique_per_session(sql, chat):
    session = sql("INSERT INTO sessions (chat_id) VALUES (:c) RETURNING id", c=chat)[0][0]
    insert = (
        "INSERT INTO messages (session_id, ordinal, role, content) "
        "VALUES (:s, :o, 'user', CAST(:content AS jsonb))"
    )
    sql(insert, s=session, o=0, content='[{"type": "text", "text": "Salom"}]')
    with pytest.raises(IntegrityError):
        sql(insert, s=session, o=0, content='[{"type": "text", "text": "again"}]')
    (content,) = sql("SELECT content FROM messages")[0]
    assert content == [{"type": "text", "text": "Salom"}]  # JSON blocks round-trip


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("UPDATE chats SET mode = 'robot'", "ck_chats_mode"),
        ("UPDATE chats SET language = 'de'", "ck_chats_language"),
    ],
)
def test_chat_values_are_checked(sql, chat, statement, constraint):
    with pytest.raises(IntegrityError, match=constraint):
        sql(statement)


def test_deleting_a_chat_removes_all_its_data(sql, chat):
    """/forget deletes the chat row; everything else goes with it."""
    session = sql("INSERT INTO sessions (chat_id) VALUES (:c) RETURNING id", c=chat)[0][0]
    sql(
        "INSERT INTO messages (session_id, ordinal, role, content) "
        "VALUES (:s, 0, 'user', '[]'::jsonb)",
        s=session,
    )
    book(sql, chat, "10:00", "11:00")
    sql("INSERT INTO handoffs (chat_id, reason, summary) VALUES (:c, 'r', 's')", c=chat)
    sql("DELETE FROM chats WHERE id = :c", c=chat)
    for table in ("sessions", "messages", "bookings", "handoffs"):
        assert sql(f"SELECT count(*) FROM {table}")[0][0] == 0, table
