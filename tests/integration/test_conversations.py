"""The conversation store and the agent against PostgreSQL."""

import json
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from receptionist.core.agent import AgentConfig, Assistant, block_to_param
from receptionist.core.clinic import load_clinic
from receptionist.core.prompts import render_system_prompt
from receptionist.core.tools import ToolContext
from receptionist.db.conversations import (
    SqlChatRepository,
    SqlConversationStore,
    SqlRelayRepository,
)
from receptionist.db.repositories import SqlBookingRepository, SqlHandoffRepository
from tests.fakes import FakeClaude, RecordingNotifier, message, text, thinking, tool_use

CLINIC = load_clinic(Path(__file__).resolve().parents[2] / "data" / "clinic.yaml")
T0 = datetime.combine(datetime(2026, 10, 5), time(10), CLINIC.tz)


@pytest.fixture
async def sessions(database_url):
    engine = create_async_engine(database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def chat_id(sessions) -> int:
    return (await SqlChatRepository(sessions).ensure(5001, "ru")).id


async def query(sessions, statement: str) -> list[tuple]:
    async with sessions() as db:
        return [tuple(row) for row in await db.execute(sql_text(statement))]


async def test_messages_round_trip_byte_identical(sessions, chat_id):
    store = SqlConversationStore(sessions)
    session = await store.start_session(chat_id, "f" * 64, T0)
    # Key order deliberately unsorted: jsonb would reorder it, json must not.
    assistant = [
        {"signature": "sig", "thinking": "", "type": "thinking"},
        {
            "id": "t1",
            "input": {"zeta": 1, "alpha": "Ўзбек", "mid": [3, 1]},
            "name": "x",
            "type": "tool_use",
        },
    ]
    user = [{"type": "text", "text": "Салом 👋"}]
    usage = {"input_tokens": 900, "output_tokens": 40}
    await store.append(session.id, "user", user, None, T0)
    await store.append(session.id, "assistant", assistant, usage, T0 + timedelta(seconds=5))

    loaded = await store.current_session(chat_id)
    assert loaded.id == session.id and loaded.fingerprint == "f" * 64
    expected = [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
    assert json.dumps(loaded.messages, ensure_ascii=False) == json.dumps(
        expected, ensure_ascii=False
    )
    assert loaded.context_tokens == 940
    assert loaded.last_activity == T0 + timedelta(seconds=5)


async def test_session_lifecycle(sessions, chat_id):
    store = SqlConversationStore(sessions)
    assert await store.current_session(chat_id) is None
    first = await store.start_session(chat_id, "a" * 64, T0)
    loaded = await store.current_session(chat_id)
    assert loaded.messages == [] and loaded.last_activity == T0
    await store.end_session(first.id, "summary text", T0 + timedelta(hours=1))
    assert await store.current_session(chat_id) is None
    second = await store.start_session(chat_id, "a" * 64, T0 + timedelta(hours=2))
    assert second.id != first.id
    rows = await query(sessions, "SELECT id, summary FROM sessions ORDER BY id")
    assert rows == [(first.id, "summary text"), (second.id, None)]


async def test_ordinals_are_sequential(sessions, chat_id):
    store = SqlConversationStore(sessions)
    session = await store.start_session(chat_id, "a" * 64, T0)
    for i, role in enumerate(["user", "assistant", "user", "assistant"]):
        await store.append(session.id, role, [{"type": "text", "text": str(i)}], None, T0)
    rows = await query(sessions, "SELECT ordinal, role FROM messages ORDER BY ordinal")
    assert rows == [(0, "user"), (1, "assistant"), (2, "user"), (3, "assistant")]


async def test_chat_records(sessions):
    chats = SqlChatRepository(sessions)
    first = await chats.ensure(42, "uz")
    assert (first.tg_chat_id, first.language, first.mode) == (42, "uz", "bot")
    assert (await chats.ensure(42, None)).language == "uz"  # unknown language keeps the old one
    assert (await chats.ensure(42, "en")).id == first.id
    assert await chats.forget(42) is True
    assert await chats.forget(42) is False
    assert await query(sessions, "SELECT count(*) FROM chats") == [(0,)]


async def test_chat_mode_lookup_and_usage_window(sessions, chat_id):
    chats = SqlChatRepository(sessions)
    assert (await chats.find(5001)).mode == "bot"
    assert await chats.find(9999) is None
    assert await chats.set_mode(chat_id, "operator", T0) is True
    assert await chats.set_mode(chat_id, "operator", T0) is False  # already
    assert (await chats.find(5001)).mode == "operator"

    store = SqlConversationStore(sessions)
    session = await store.start_session(chat_id, "a" * 64, T0)
    usage = {"input_tokens": 10, "output_tokens": 5}
    await store.append(session.id, "user", [{"type": "text", "text": "q"}], None, T0)
    await store.append(session.id, "assistant", [{"type": "text", "text": "old"}], usage, T0)
    later = T0 + timedelta(hours=30)
    await store.append(session.id, "assistant", [{"type": "text", "text": "new"}], usage, later)
    assert await chats.usage_since(chat_id, later - timedelta(hours=24)) == [usage]
    assert len(await chats.usage_since(chat_id, T0)) == 2


async def test_display_name_and_lookup_by_id(sessions):
    chats = SqlChatRepository(sessions)
    chat = await chats.ensure(77, "en", "Aziz @aziz")
    assert chat.label == f"Aziz @aziz (#{chat.id})"
    assert (await chats.ensure(77, "ru")).display_name == "Aziz @aziz"  # kept when unknown
    assert (await chats.ensure(77, "ru", "Aziz K")).display_name == "Aziz K"
    assert (await chats.get(chat.id)).tg_chat_id == 77
    assert await chats.get(chat.id + 1000) is None


async def test_returning_to_the_bot_closes_open_handoffs(sessions, chat_id):
    chats = SqlChatRepository(sessions)
    handoffs = SqlHandoffRepository(sessions)
    first = await handoffs.open(chat_id, "complaint", "Unhappy.")
    await handoffs.set_admin_message(first, 9001)
    assert (await chats.get(chat_id)).mode == "operator"

    assert await chats.set_mode(chat_id, "bot", T0) is True
    rows = await query(sessions, "SELECT id, admin_message_id, closed_at FROM handoffs")
    assert rows == [(first, 9001, T0)]
    assert (await chats.get(chat_id)).mode == "bot"


async def test_relay_map(sessions, chat_id):
    relays = SqlRelayRepository(sessions)
    await relays.record(-100, 10, chat_id)
    await relays.record(-100, 10, chat_id)  # recording twice is harmless
    assert await relays.chat_for(-100, 10) == chat_id
    assert await relays.chat_for(-100, 11) is None
    assert await relays.chat_for(-200, 10) is None  # another group
    await SqlChatRepository(sessions).forget(5001)
    assert await relays.chat_for(-100, 10) is None  # /forget removes the mapping


async def test_agent_with_the_database_replays_history_exactly(sessions, chat_id):
    store = SqlConversationStore(sessions)
    clock = {"now": T0}
    first = message(
        thinking("sig-a"),
        tool_use(
            "toolu_1",
            "find_free_slots",
            {"service_id": "filling", "date_from": "2026-10-06", "days": 1},
        ),
        stop_reason="tool_use",
    )
    second = message(thinking("sig-b"), text("Есть 09:00."))
    client = FakeClaude(first, second, message(text("Записываю.")))

    def tools(chat: int) -> ToolContext:
        return ToolContext(
            chat,
            CLINIC,
            SqlBookingRepository(sessions),
            SqlHandoffRepository(sessions),
            RecordingNotifier(),
            clock=lambda: clock["now"],
        )

    assistant = Assistant(
        client,
        AgentConfig(model="claude-opus-5-5"),
        CLINIC,
        render_system_prompt(CLINIC, "103"),
        store,
        tools,
        clock=lambda: clock["now"],
    )
    await assistant.reply(chat_id, "Пломба завтра?", "ru")
    clock["now"] += timedelta(minutes=1)
    await assistant.reply(chat_id, "Давайте 09:00", "ru")

    previous = [
        *client.requests[1]["messages"],
        {"role": "assistant", "content": [block_to_param(b) for b in second.content]},
    ]
    replayed = client.requests[2]["messages"][: len(previous)]
    assert json.dumps(replayed, ensure_ascii=False) == json.dumps(previous, ensure_ascii=False)
    assert "09:00" in client.requests[1]["messages"][2]["content"][0]["content"]
