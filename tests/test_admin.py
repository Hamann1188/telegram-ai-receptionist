"""The admin desk: notifications, handoff relay and the take-over / return buttons."""

from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from receptionist.core.clinic import load_clinic
from receptionist.core.ports import BookingRecord
from receptionist.db.conversations import ChatRecord
from receptionist.telegram import admin_texts as t
from receptionist.telegram.admin import RETURN, TAKE_OVER, AdminCallback, AdminDesk
from receptionist.telegram.texts import BACK_TO_BOT, STAFF_JOINED
from tests.fakes import FakeChats, FakeHandoffs, FakeRelays

CLINIC = load_clinic(Path(__file__).resolve().parents[1] / "data" / "clinic.yaml")
NOW = datetime.combine(datetime(2026, 10, 5), time(10), CLINIC.tz)
ADMIN = -1001234567890
PATIENT_TG = 555


def forbidden() -> TelegramForbiddenError:
    return TelegramForbiddenError(SendMessage(chat_id=1, text="x"), "bot was blocked by the user")


class FakeBot:
    """Records what the desk sends; message ids count up from 100."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.copied: list[dict] = []
        self.reactions: list[int] = []
        self.blocked: set[int] = set()  # chat ids that raise Forbidden
        self._next_id = 100

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        if chat_id in self.blocked:
            raise forbidden()
        message_id = self._id()
        self.sent.append(
            {"chat_id": chat_id, "text": text, "markup": reply_markup, "message_id": message_id}
        )
        return SimpleNamespace(message_id=message_id)

    async def copy_message(self, chat_id, from_chat_id, message_id, **kwargs):
        if chat_id in self.blocked:
            raise forbidden()
        new_id = self._id()
        self.copied.append(
            {"to": chat_id, "from": from_chat_id, "message_id": message_id, **kwargs}
        )
        return SimpleNamespace(message_id=new_id)

    async def set_message_reaction(self, chat_id, message_id, reaction):
        self.reactions.append(message_id)

    def to(self, chat_id) -> list[dict]:
        return [m for m in self.sent if m["chat_id"] == chat_id]


class FakeAssistant:
    def __init__(self) -> None:
        self.resets: list[int] = []

    async def reset(self, chat_id) -> None:
        self.resets.append(chat_id)


@pytest.fixture
def env():
    env = SimpleNamespace(
        bot=FakeBot(),
        chats=FakeChats(),
        relays=FakeRelays(),
        handoffs=FakeHandoffs(),
        assistant=FakeAssistant(),
    )
    env.desk = AdminDesk(
        env.bot, ADMIN, "en", CLINIC, env.chats, env.relays, env.handoffs, clock=lambda: NOW
    )
    env.desk.attach(env.assistant)
    return env


async def patient(env, mode="bot", language="ru") -> ChatRecord:
    chat = await env.chats.ensure(PATIENT_TG, language, "Aziz @aziz")
    if mode != "bot":
        await env.chats.set_mode(chat.id, mode, NOW)
    return await env.chats.get(chat.id)


def button(message: dict) -> AdminCallback:
    (row,) = message["markup"].inline_keyboard
    (key,) = row
    return AdminCallback.unpack(key.callback_data)


def booking(chat_id: int) -> BookingRecord:
    start = NOW + timedelta(days=2)  # Wednesday 10:00
    return BookingRecord(
        7,
        chat_id,
        "hygiene",
        "hygienist",
        start,
        start + timedelta(hours=1),
        "Aziz",
        "+998901234567",
        "confirmed",
    )


# --- notifications -------------------------------------------------------------


async def test_booking_card_with_take_over_button(env):
    chat = await patient(env)
    await env.desk.booking_created(booking(chat.id))
    (card,) = env.bot.to(ADMIN)
    assert card["text"] == (
        "🗓 New booking #7\nProfessional teeth cleaning\nWed, 07.10.2026, 10:00–11:00\n"
        "Aziz, +998901234567\nChat: Aziz @aziz (#1)"
    )
    assert button(card) == AdminCallback(action=TAKE_OVER, chat_id=chat.id)
    assert env.relays.map[(ADMIN, card["message_id"])] == chat.id


async def test_cancellation_card_in_russian(env):
    env.desk = AdminDesk(env.bot, ADMIN, "ru", CLINIC, env.chats, env.relays, env.handoffs)
    chat = await patient(env)
    await env.desk.booking_cancelled(booking(chat.id))
    (card,) = env.bot.to(ADMIN)
    assert card["text"].startswith(
        "❌ Запись #7 отменена\nПрофессиональная гигиена полости рта\nср,"
    )


async def test_handoff_card_with_return_button(env):
    chat = await patient(env, mode="operator")
    await env.desk.handoff_requested(chat.id, 3, "emergency", "Swelling, told to call 103.")
    (card,) = env.bot.to(ADMIN)
    assert card["text"].startswith(
        "🙋 A patient needs a staff member (URGENT)\nChat: Aziz @aziz (#1)"
    )
    assert "Swelling, told to call 103." in card["text"]
    assert button(card) == AdminCallback(action=RETURN, chat_id=chat.id)
    assert env.handoffs.admin_messages == {3: card["message_id"]}
    assert env.relays.map[(ADMIN, card["message_id"])] == chat.id


async def test_notifications_for_a_forgotten_chat_are_skipped(env):
    await env.desk.booking_created(booking(99))
    await env.desk.handoff_requested(99, 1, "other", "x")
    assert env.bot.sent == []


# --- relay ---------------------------------------------------------------------


async def test_patient_text_is_forwarded_and_mapped(env):
    chat = await patient(env, mode="operator")
    assert await env.desk.forward_text(chat, "Когда придёт врач?") is True
    (message,) = env.bot.to(ADMIN)
    assert message["text"] == "💬 Aziz @aziz (#1):\nКогда придёт врач?"
    assert env.relays.map[(ADMIN, message["message_id"])] == chat.id


async def test_patient_file_is_copied_under_a_header(env):
    chat = await patient(env, mode="operator")
    assert await env.desk.forward_file(chat, 42) is True
    (header,) = env.bot.to(ADMIN)
    (copy,) = env.bot.copied
    assert (copy["to"], copy["from"], copy["message_id"]) == (ADMIN, PATIENT_TG, 42)
    assert copy["reply_parameters"].message_id == header["message_id"]
    assert len(env.relays.map) == 2  # header and the copy both lead to the patient


async def test_unreachable_admin_group_reports_failure(env):
    chat = await patient(env, mode="operator")
    env.bot.blocked.add(ADMIN)
    assert await env.desk.forward_text(chat, "hi") is False
    assert await env.desk.forward_file(chat, 42) is False


async def test_operator_reply_is_copied_to_the_patient(env):
    chat = await patient(env, mode="operator")
    await env.desk.forward_text(chat, "Hello?")
    patient_message = env.bot.to(ADMIN)[0]["message_id"]

    assert await env.desk.operator_reply(patient_message, 500) is None
    (copy,) = env.bot.copied
    assert (copy["to"], copy["from"], copy["message_id"]) == (PATIENT_TG, ADMIN, 500)
    assert env.bot.reactions == [500]
    assert env.relays.map[(ADMIN, 500)] == chat.id  # replying to it works too


async def test_operator_reply_while_the_bot_handles_the_chat(env):
    chat = await patient(env)
    await env.desk.booking_created(booking(chat.id))
    card = env.bot.to(ADMIN)[0]["message_id"]
    assert await env.desk.operator_reply(card, 500) == t.NOT_OPERATOR_MODE["en"]
    assert env.bot.copied == []


async def test_operator_reply_to_an_unknown_message(env):
    assert await env.desk.operator_reply(12345, 500) == t.UNKNOWN_CHAT["en"]


async def test_operator_reply_to_a_patient_who_blocked_the_bot(env):
    chat = await patient(env, mode="operator")
    await env.desk.forward_text(chat, "Hello?")
    env.bot.blocked.add(PATIENT_TG)
    notice = await env.desk.operator_reply(env.bot.to(ADMIN)[0]["message_id"], 500)
    assert notice == t.DELIVERY_FAILED["en"]


# --- take over and return --------------------------------------------------------


async def test_take_over_and_return_round_trip(env):
    chat = await patient(env, language="uz")

    changed, notice = await env.desk.take_over(chat.id, "Malika")
    assert (changed, notice) == (True, t.DONE["en"])
    assert (await env.chats.get(chat.id)).mode == "operator"
    assert env.handoffs.opened == [(chat.id, "staff_takeover", "Taken over by Malika.")]
    taken = env.bot.to(ADMIN)[-1]
    assert taken["text"].startswith("👤 Malika took over chat Aziz @aziz (#1).")
    assert button(taken) == AdminCallback(action=RETURN, chat_id=chat.id)
    assert env.bot.to(PATIENT_TG)[-1]["text"] == STAFF_JOINED["uz"]

    assert await env.desk.take_over(chat.id, "Malika") == (False, t.ALREADY_OPERATOR["en"])

    changed, _ = await env.desk.return_to_bot(chat.id, "Malika")
    assert changed and (await env.chats.get(chat.id)).mode == "bot"
    assert env.assistant.resets == [chat.id]  # a fresh conversation after the handoff
    assert env.bot.to(PATIENT_TG)[-1]["text"] == BACK_TO_BOT["uz"]
    assert env.bot.to(ADMIN)[-1]["text"] == "🤖 Malika returned chat Aziz @aziz (#1) to the bot."

    assert await env.desk.return_to_bot(chat.id, "Malika") == (False, t.ALREADY_BOT["en"])


async def test_buttons_for_a_deleted_chat(env):
    assert await env.desk.take_over(99, "Malika") == (False, t.UNKNOWN_CHAT["en"])
    assert await env.desk.return_to_bot(99, "Malika") == (False, t.UNKNOWN_CHAT["en"])


async def test_patient_blocking_the_bot_does_not_break_take_over(env):
    chat = await patient(env)
    env.bot.blocked.add(PATIENT_TG)
    changed, _ = await env.desk.take_over(chat.id, "Malika")
    assert changed and (await env.chats.get(chat.id)).mode == "operator"


async def test_patient_restart_note(env):
    chat = await patient(env)
    await env.desk.patient_restarted(chat)
    assert env.bot.to(ADMIN)[0]["text"] == (
        "🤖 Patient Aziz @aziz (#1) restarted the bot (/start); the bot handles the chat again."
    )


# --- texts -----------------------------------------------------------------------


def test_admin_texts_exist_in_three_languages():
    for name in dir(t):
        value = getattr(t, name)
        if isinstance(value, dict) and name.isupper():
            assert set(value) == {"ru", "uz", "en"}, name


def test_every_handoff_reason_has_a_label():
    from receptionist.core.tools import HANDOFF_REASONS

    for labels in t.REASONS.values():
        assert set(HANDOFF_REASONS) <= set(labels)


def test_admin_callback_fits_telegram_limit():
    packed = AdminCallback(action=RETURN, chat_id=9_223_372_036_854_775_807).pack()
    assert len(packed.encode()) <= 64
