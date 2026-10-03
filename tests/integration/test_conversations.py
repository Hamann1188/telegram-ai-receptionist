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
from receptionist.db.conversations import SqlChatRepository, SqlConversationStore
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
