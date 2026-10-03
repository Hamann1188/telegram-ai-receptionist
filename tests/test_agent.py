import asyncio
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from receptionist.core.agent import AgentConfig, Assistant, block_to_param, render_transcript
from receptionist.core.clinic import load_clinic
from receptionist.core.prompts import SUMMARY_SYSTEM, render_system_prompt
from receptionist.core.tools import ToolContext
from tests.fakes import (
    FakeBookings,
    FakeClaude,
    FakeConversationStore,
    FakeHandoffs,
    RecordingNotifier,
    message,
    text,
    thinking,
    tool_use,
)

CLINIC = load_clinic(Path(__file__).resolve().parents[1] / "data" / "clinic.yaml")
SYSTEM = render_system_prompt(CLINIC, "103")
CHAT = 7


class Clock:
    def __init__(self) -> None:
        self.now = datetime.combine(datetime(2026, 10, 5), time(10, 0), CLINIC.tz)  # Monday

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def env():
    clock = Clock()
    return SimpleNamespace(
        clock=clock,
        store=FakeConversationStore(),
        bookings=FakeBookings(),
        handoffs=FakeHandoffs(),
        notifier=RecordingNotifier(),
    )


def make_assistant(client, env, system=SYSTEM, **config) -> Assistant:
    return Assistant(
        client,
        AgentConfig(model="claude-opus-5-5", **config),
        CLINIC,
        system,
        env.store,
        lambda chat_id: ToolContext(
            chat_id, CLINIC, env.bookings, env.handoffs, env.notifier, clock=env.clock
        ),
        clock=env.clock,
    )


def api_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.test"))


def assistant_turn(response) -> dict:
    return {"role": "assistant", "content": [block_to_param(b) for b in response.content]}


# --- request shape -------------------------------------------------------------


async def test_text_reply_and_request_shape(env):
    client = FakeClaude(message(thinking(), text("Здравствуйте! Чем помочь?")))
    reply = await make_assistant(client, env).reply(CHAT, "Привет", language="ru")

    assert (reply.outcome, reply.text, reply.handoff) == (
        "answered",
        "Здравствуйте! Чем помочь?",
        False,
    )
    (request,) = client.requests
    assert request["model"] == "claude-opus-5-5"
    assert request["system"] == SYSTEM
    assert request["output_config"] == {"effort": "low"}
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["max_tokens"] == 8000
    # Claude Opus 5.5: no forced tool choice and no thinking switch-off (both 400).
    assert "tool_choice" not in request and "thinking" not in request
    assert len(request["tools"]) == 7 and all(t["strict"] for t in request["tools"])

    (user,) = request["messages"]
    context, patient = user["content"]
    assert context["text"] == (
        "<context>Current time: Monday, 2026-10-05 10:00 (Asia/Tashkent). "
        "Patient's Telegram app language: Russian.</context>"
    )
    assert patient == {"type": "text", "text": "Привет"}


async def test_reply_cost_and_usage(env):
    client = FakeClaude(message(text("Hi"), input_tokens=1000, output_tokens=50, cache_read=2000))
    reply = await make_assistant(client, env).reply(CHAT, "Hi")
    assert reply.usage["cache_read_input_tokens"] == 2000
    assert reply.cost_usd == pytest.approx((1000 * 4 + 50 * 20 + 2000 * 0.2) / 1_000_000)


# --- tool loop and history -------------------------------------------------------


async def test_tool_loop_replays_the_assistant_turn_verbatim(env):
    first = message(
        thinking("sig-a"), tool_use("toolu_1", "list_services", {}), stop_reason="tool_use"
    )
    second = message(thinking("sig-b"), text("Чистка стоит 450 000 сум."))
    client = FakeClaude(first, second)

    reply = await make_assistant(client, env).reply(CHAT, "Сколько стоит чистка?", "ru")

    assert reply.text == "Чистка стоит 450 000 сум."
    req1, req2 = client.requests
    assert req2["messages"][0] == req1["messages"][0]
    assert req2["messages"][1] == assistant_turn(first)
    assert req2["messages"][1]["content"][0] == thinking("sig-a")  # thinking kept, signature too
    (result,) = req2["messages"][2]["content"]
    assert (result["type"], result["tool_use_id"], result["is_error"]) == (
        "tool_result",
        "toolu_1",
        False,
    )
    assert "450000" in result["content"]
    # The store holds exactly what was sent, plus the final answer.
    assert env.store.history(1) == [*req2["messages"], assistant_turn(second)]


async def test_next_turn_sends_the_stored_history_byte_identical(env):
    first = message(
        thinking("sig-a"),
        tool_use(
            "toolu_1",
            "find_free_slots",
            {"service_id": "filling", "date_from": "2026-10-06", "days": 1},
        ),
        stop_reason="tool_use",
    )
    second = message(thinking("sig-b"), text("Есть 09:00 и 09:30."))
    third = message(text("Хорошо."))
    client = FakeClaude(first, second, third)
    assistant = make_assistant(client, env)

    await assistant.reply(CHAT, "Запишите на пломбу завтра", "ru")
    env.clock.now += timedelta(minutes=2)
    await assistant.reply(CHAT, "Давайте 09:00", "ru")

    previous = [*client.requests[1]["messages"], assistant_turn(second)]
    sent = client.requests[2]["messages"]
    assert json.dumps(sent[: len(previous)], ensure_ascii=False) == json.dumps(
        previous, ensure_ascii=False
    )
    # Tool input key order survives the round trip (it is rendered as JSON for the model).
    assert list(sent[1]["content"][1]["input"]) == ["service_id", "date_from", "days"]
    assert sent[-1]["content"][-1] == {"type": "text", "text": "Давайте 09:00"}
    assert client.requests[2]["system"] == client.requests[0]["system"]
    assert client.requests[2]["tools"] == client.requests[0]["tools"]


async def test_parallel_tool_calls_get_results_in_order(env):
    first = message(
        tool_use("toolu_a", "get_clinic_info", {"topic": "parking"}),
        tool_use("toolu_b", "list_services", {}),
        stop_reason="tool_use",
    )
    client = FakeClaude(first, message(text("OK")))
    await make_assistant(client, env).reply(CHAT, "Parking and prices?")
    results = client.requests[1]["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["toolu_a", "toolu_b"]
    assert "courtyard" in results[0]["content"]


async def test_tool_errors_are_returned_to_the_model(env):
    booking = {
        "service_id": "filling",
        "date": "2026-10-06",
        "time": "10:00",
        "patient_name": "Aziz",
        "phone": "901234567",
        "confirmed": False,
    }
    first = message(tool_use("toolu_1", "create_booking", booking), stop_reason="tool_use")
    client = FakeClaude(first, message(text("Подтвердите, пожалуйста.")))
    await make_assistant(client, env).reply(CHAT, "Запишите", "ru")
    (result,) = client.requests[1]["messages"][2]["content"]
    assert result["is_error"] is True and "confirmed=true" in result["content"]
    assert env.bookings.rows == []


async def test_handoff_is_reported(env):
    first = message(
        tool_use("toolu_1", "handoff_to_human", {"reason": "complaint", "summary": "Unhappy."}),
        stop_reason="tool_use",
    )
    client = FakeClaude(first, message(text("Сотрудник скоро ответит.")))
    reply = await make_assistant(client, env).reply(CHAT, "Позовите человека", "ru")
    assert reply.handoff is True and reply.outcome == "answered"
    assert env.handoffs.opened == [(CHAT, "complaint", "Unhappy.")]


# --- failure paths -------------------------------------------------------------


async def test_refusal_gets_a_polite_reply_and_a_fresh_session(env):
    client = FakeClaude(message(stop_reason="refusal"), message(text("Hello again")))
    assistant = make_assistant(client, env)
    reply = await assistant.reply(CHAT, "something declined", "en")
    assert reply.outcome == "refusal" and "connect you" in reply.text
    assert env.store.sessions[1]["ended_at"] is not None
    assert len(env.store.history(1)) == 1  # the refused turn wasn't stored

    await assistant.reply(CHAT, "Hi", "en")
    assert len(client.requests[1]["messages"]) == 1  # new session


async def test_truncated_tool_call_is_not_run(env):
    booking = {
        "service_id": "filling",
        "date": "2026-10-06",
        "time": "10:00",
        "patient_name": "Aziz",
        "phone": "901234567",
        "confirmed": True,
    }
    truncated = message(tool_use("toolu_1", "create_booking", booking), stop_reason="max_tokens")
    reply = await make_assistant(FakeClaude(truncated), env).reply(CHAT, "Book", "uz")
    assert reply.outcome == "incomplete" and reply.text.startswith("Kechirasiz")
    assert env.bookings.rows == []
    assert env.store.sessions[1]["ended_at"] is not None


async def test_iteration_cap_stops_the_loop_with_a_valid_history(env):
    loop = [
        message(tool_use(f"toolu_{i}", "list_services", {}), stop_reason="tool_use")
        for i in range(6)
    ]
    client = FakeClaude(*loop)
    reply = await make_assistant(client, env).reply(CHAT, "?", "en")
    assert reply.outcome == "incomplete" and len(client.requests) == 6
    history = env.store.history(1)
    assert history[-1]["role"] == "user" and history[-1]["content"][0]["type"] == "tool_result"
    tool_ids = [b["id"] for m in history for b in m["content"] if b["type"] == "tool_use"]
    result_ids = [
        b["tool_use_id"] for m in history for b in m["content"] if b["type"] == "tool_result"
    ]
    assert tool_ids == result_ids  # every call answered, in order


async def test_api_error_keeps_the_patient_message(env):
    reply = await make_assistant(FakeClaude(api_error()), env).reply(CHAT, "Salom", "uz")
    assert reply.outcome == "error" and CLINIC.phone in reply.text
    assert [m["role"] for m in env.store.history(1)] == ["user"]
    assert env.store.sessions[1]["ended_at"] is None


async def test_reply_without_text_ends_the_session(env):
    reply = await make_assistant(FakeClaude(message(thinking())), env).reply(CHAT, "Hi")
    assert reply.outcome == "incomplete"
    assert env.store.sessions[1]["ended_at"] is not None


# --- sessions ------------------------------------------------------------------


async def test_idle_session_is_rotated(env):
    client = FakeClaude(message(text("A")), message(text("B")))
    assistant = make_assistant(client, env)
    await assistant.reply(CHAT, "one")
    env.clock.now += timedelta(hours=13)
    await assistant.reply(CHAT, "two")
    assert len(client.requests[1]["messages"]) == 1
    assert env.store.sessions[1]["ended_at"] is not None and 2 in env.store.sessions


async def test_changed_prompt_starts_a_new_session(env):
    await make_assistant(FakeClaude(message(text("A"))), env).reply(CHAT, "one")
    client = FakeClaude(message(text("B")))
    await make_assistant(client, env, system=render_system_prompt(CLINIC, "112")).reply(CHAT, "two")
    assert len(client.requests[0]["messages"]) == 1
    assert env.store.sessions[1]["ended_at"] is not None


async def test_full_context_is_summarized_into_the_next_session(env):
    big = message(text("A"), input_tokens=40_000)
    summary = message(text("Patient Aziz booked a filling, booking 3."))
    client = FakeClaude(big, summary, message(text("B")))
    assistant = make_assistant(client, env)
    await assistant.reply(CHAT, "one")
    await assistant.reply(CHAT, "two")

    summary_request = client.requests[1]
    assert summary_request["system"] == SUMMARY_SYSTEM and "tools" not in summary_request
    assert "Patient: one" in summary_request["messages"][0]["content"]
    first_block = client.requests[2]["messages"][0]["content"][0]["text"]
    assert first_block == (
        "<previous_conversation_summary>Patient Aziz booked a filling, booking 3."
        "</previous_conversation_summary>"
    )
    assert env.store.sessions[1]["summary"] == "Patient Aziz booked a filling, booking 3."


async def test_failed_summary_still_rotates(env):
    client = FakeClaude(message(text("A"), input_tokens=40_000), api_error(), message(text("B")))
    assistant = make_assistant(client, env)
    await assistant.reply(CHAT, "one")
    reply = await assistant.reply(CHAT, "two")
    assert reply.outcome == "answered"
    assert len(client.requests[2]["messages"]) == 1
    assert "previous_conversation_summary" not in json.dumps(client.requests[2]["messages"])


async def test_reset_ends_the_session(env):
    client = FakeClaude(message(text("A")), message(text("B")))
    assistant = make_assistant(client, env)
    await assistant.reply(CHAT, "one")
    await assistant.reset(CHAT)
    await assistant.reply(CHAT, "two")
    assert len(client.requests[1]["messages"]) == 1


async def test_concurrent_messages_of_one_chat_are_handled_in_turn(env):
    client = FakeClaude(message(text("A")), message(text("B")), delay=0.01)
    assistant = make_assistant(client, env)
    await asyncio.gather(assistant.reply(CHAT, "one"), assistant.reply(CHAT, "two"))
    assert [m["role"] for m in env.store.history(1)] == ["user", "assistant", "user", "assistant"]
    assert len(client.requests[1]["messages"]) == 3


async def test_different_chats_have_separate_sessions(env):
    client = FakeClaude(message(text("A")), message(text("B")))
    assistant = make_assistant(client, env)
    await assistant.reply(1, "one")
    await assistant.reply(2, "two")
    assert len(client.requests[1]["messages"]) == 1


def test_render_transcript():
    history = [
        {"role": "user", "content": [text("<context>…</context>"), text("Book a filling")]},
        {"role": "assistant", "content": [thinking(), tool_use("t", "list_services", {})]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "{}"}]},
        {"role": "assistant", "content": [text("Done")]},
    ]
    assert render_transcript(history).splitlines() == [
        "Patient: <context>…</context>",
        "Patient: Book a filling",
        "[tool call list_services {}]",
        "[tool result {}]",
        "Receptionist: Done",
    ]
