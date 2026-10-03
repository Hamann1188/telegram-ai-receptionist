import asyncio
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from receptionist.config import Settings
from receptionist.core.agent import AgentReply
from receptionist.core.clinic import load_clinic
from receptionist.core.ports import NewBooking
from receptionist.db.conversations import ChatRecord
from receptionist.telegram import texts
from receptionist.telegram.bot import NO_TOKEN, build_dispatcher
from receptionist.telegram.handlers import ForgetCallback, start, unsupported
from receptionist.telegram.ratelimit import RateLimiter
from receptionist.telegram.service import ChatService
from receptionist.telegram.texts import detect_language, language_from_code, split_message
from tests.fakes import FakeBookings, RecordingNotifier

CLINIC = load_clinic(Path(__file__).resolve().parents[1] / "data" / "clinic.yaml")
NOW = datetime.combine(datetime(2026, 10, 5), time(10), CLINIC.tz)
TG = 555


# --- language ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "language"),
    [
        ("ru", "ru"),
        ("uz", "uz"),
        ("en", "en"),
        ("en-US", "en"),
        ("RU", "ru"),
        ("de", "en"),
        (None, "en"),
    ],
)
def test_language_from_code(code, language):
    assert language_from_code(code) == language


@pytest.mark.parametrize(
    ("text", "fallback", "expected"),
    [
        ("Сколько стоит чистка?", "en", "ru"),
        ("Қанча туради?", "ru", "uz"),  # Uzbek Cyrillic letter
        ("Salom, qancha turadi?", "ru", "uz"),
        ("Tishim og'riyapti", "en", "uz"),
        ("Ertaga soat 10 da bo‘sh vaqt bormi?", "en", "uz"),
        ("How much is a cleaning?", "ru", "en"),
        ("12:00", "uz", "uz"),  # no letters: keep the fallback
        ("👍", "ru", "ru"),
    ],
)
def test_detect_language(text, fallback, expected):
    assert detect_language(text, fallback) == expected


def test_every_text_exists_in_three_languages():
    for name in dir(texts):
        value = getattr(texts, name)
        if isinstance(value, dict) and name.isupper():
            assert set(value) == {"ru", "uz", "en"}, name


def test_greetings_say_the_clinic_is_fictional():
    markers = {"ru": "вымышленная", "uz": "o'ylab topilgan", "en": "fictional"}
    for language, text in texts.GREETING.items():
        assert markers[language] in text


def test_split_message_keeps_short_text_whole():
    assert split_message("  Hello  ") == ["Hello"]
    assert split_message("") == []


def test_split_message_prefers_paragraph_breaks():
    paragraph = "word " * 30
    parts = split_message("\n\n".join([paragraph] * 5), limit=400)
    assert all(len(p) <= 400 for p in parts) and len(parts) > 1
    assert " ".join(parts).split() == ("\n\n".join([paragraph] * 5)).split()


def test_split_message_cuts_text_without_spaces():
    parts = split_message("x" * 9000)
    assert [len(p) for p in parts] == [4096, 4096, 808]


# --- rate limit ----------------------------------------------------------------


def test_rate_limiter_sliding_window():
    clock = SimpleNamespace(t=0.0)
    limiter = RateLimiter(3, 10, clock=lambda: clock.t)
    assert [limiter.allow(1) for _ in range(4)] == [True, True, True, False]
    assert limiter.allow(2) is True  # other chats are independent
    clock.t = 9.9
    assert limiter.allow(1) is False
    clock.t = 10.0
    assert limiter.allow(1) is True


# --- service -------------------------------------------------------------------


class FakeAssistant:
    def __init__(self, reply_text="Ответ", fail=False):
        self.replies: list[tuple] = []
        self.resets: list[int] = []
        self.reply_text = reply_text
        self.fail = fail
        self._locks: dict[int, asyncio.Lock] = {}

    def lock(self, chat_id):
        return self._locks.setdefault(chat_id, asyncio.Lock())

    async def reply(self, chat_id, text, language):
        if self.fail:
            raise RuntimeError("database down")
        self.replies.append((chat_id, text, language))
        return AgentReply(text=self.reply_text, outcome="answered")

    async def reset(self, chat_id):
        self.resets.append(chat_id)


class FakeChats:
    def __init__(self):
        self.rows: dict[int, ChatRecord] = {}
        self.usage: list[dict] = []
        self.forgotten: list[int] = []

    async def ensure(self, tg_chat_id, language):
        old = self.rows.get(tg_chat_id)
        record = ChatRecord(
            id=old.id if old else len(self.rows) + 1,
            tg_chat_id=tg_chat_id,
            language=language or (old.language if old else None),
            mode=old.mode if old else "bot",
        )
        self.rows[tg_chat_id] = record
        return record

    async def find(self, tg_chat_id):
        return self.rows.get(tg_chat_id)

    async def set_mode(self, chat_id, mode):
        for tg, row in self.rows.items():
            if row.id == chat_id:
                self.rows[tg] = ChatRecord(row.id, tg, row.language, mode)

    async def usage_since(self, chat_id, since):
        return self.usage

    async def forget(self, tg_chat_id):
        self.forgotten.append(tg_chat_id)
        return self.rows.pop(tg_chat_id, None) is not None


@pytest.fixture
def deps():
    return SimpleNamespace(
        assistant=FakeAssistant(),
        chats=FakeChats(),
        bookings=FakeBookings(),
        notifier=RecordingNotifier(),
    )


def make_service(deps, limit=20, **kwargs) -> ChatService:
    return ChatService(
        deps.assistant,
        deps.chats,
        deps.bookings,
        deps.notifier,
        CLINIC,
        RateLimiter(limit, 600),
        model="claude-opus-5-5",
        clock=lambda: NOW,
        **kwargs,
    )


async def test_start_greets_and_resets_the_session(deps):
    deps.chats.rows[TG] = ChatRecord(1, TG, "ru", "operator")
    greeting = await make_service(deps).start(TG, "uz")
    assert greeting == texts.GREETING["uz"]
    assert deps.assistant.resets == [1]
    assert deps.chats.rows[TG].mode == "bot"  # /start leaves operator mode


async def test_text_goes_to_the_assistant_with_the_detected_language(deps):
    replies = await make_service(deps).text(TG, "en", "Сколько стоит чистка?")
    assert replies == ["Ответ"]
    assert deps.assistant.replies == [(1, "Сколько стоит чистка?", "ru")]
    assert deps.chats.rows[TG].language == "ru"


async def test_long_replies_are_split(deps):
    deps.assistant.reply_text = "слово " * 1500
    replies = await make_service(deps).text(TG, "ru", "Расскажите всё")
    assert len(replies) == 3 and all(len(r) <= 4096 for r in replies)


async def test_rate_limit(deps):
    service = make_service(deps, limit=2)
    await service.text(TG, "ru", "раз")
    await service.text(TG, "ru", "два")
    assert await service.text(TG, "ru", "три") == [texts.RATE_LIMITED["ru"]]
    assert len(deps.assistant.replies) == 2


async def test_too_long_message_is_rejected(deps):
    replies = await make_service(deps, max_message_chars=10).text(TG, "en", "x" * 11)
    assert replies == [texts.TOO_LONG["en"].format(limit=10)] and deps.assistant.replies == []


async def test_operator_mode_does_not_call_the_assistant(deps):
    deps.chats.rows[TG] = ChatRecord(1, TG, "ru", "operator")
    assert await make_service(deps).text(TG, "ru", "Алло?") == [texts.OPERATOR_MODE["ru"]]
    assert deps.assistant.replies == []


async def test_daily_budget(deps):
    deps.chats.usage = [{"input_tokens": 0, "output_tokens": 30_000}]  # $0.60
    replies = await make_service(deps).text(TG, "en", "Hello")
    assert replies == [texts.DAILY_LIMIT["en"].format(phone=CLINIC.phone)]
    assert deps.assistant.replies == []


async def test_unexpected_failure_gets_a_safe_reply(deps):
    deps.assistant.fail = True
    (reply,) = await make_service(deps).text(TG, "ru", "Привет")
    assert CLINIC.phone in reply and "database" not in reply


def test_unsupported_media_text(deps):
    assert make_service(deps).unsupported("uz") == texts.UNSUPPORTED["uz"]


async def seed_booking(deps, chat_id: int, start: datetime):
    service = CLINIC.service("hygiene")
    await deps.bookings.create(
        NewBooking(
            chat_id, service.id, service.resource, start, start + timedelta(hours=1), "A", "+998"
        )
    )


async def test_forget_prompt(deps):
    service = make_service(deps)
    assert await service.forget_prompt(TG, "ru") == (texts.NOTHING_STORED["ru"], False)

    await deps.chats.ensure(TG, "ru")
    question, ask = await service.forget_prompt(TG, "ru")
    assert ask and question == texts.FORGET_CONFIRM["ru"]

    await seed_booking(deps, 1, NOW + timedelta(days=1))
    question, _ = await service.forget_prompt(TG, "ru")
    assert question.endswith(texts.FORGET_CANCELS_BOOKINGS["ru"].format(count=1))


async def test_forget_cancels_bookings_and_deletes_the_chat(deps):
    await deps.chats.ensure(TG, "en")
    await seed_booking(deps, 1, NOW + timedelta(days=1))
    await seed_booking(deps, 1, NOW - timedelta(days=1))  # past: left alone
    assert await make_service(deps).forget(TG, "en") == texts.FORGET_DONE["en"]
    assert [b.status for b in deps.bookings.rows] == ["cancelled", "confirmed"]
    assert deps.notifier.events == [("cancelled", 1)]
    assert deps.chats.forgotten == [TG] and TG not in deps.chats.rows
    assert not deps.assistant.lock(1).locked()


async def test_forget_without_data(deps):
    assert await make_service(deps).forget(TG, "uz") == texts.NOTHING_STORED["uz"]


# --- aiogram wiring ------------------------------------------------------------


class FakeMessage:
    def __init__(self, language_code: str | None):
        self.from_user = SimpleNamespace(language_code=language_code)
        self.chat = SimpleNamespace(id=TG)
        self.sent: list[str] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.sent.append(text)


def test_dispatcher_handles_messages_and_buttons():
    assert sorted(build_dispatcher().resolve_used_update_types()) == ["callback_query", "message"]


async def test_start_handler(deps):
    message = FakeMessage("ru")
    await start(message, make_service(deps))
    assert message.sent == [texts.GREETING["ru"]]


async def test_unsupported_handler(deps):
    message = FakeMessage("en")
    await unsupported(message, make_service(deps))
    assert message.sent == [texts.UNSUPPORTED["en"]]


def test_forget_callback_data_round_trip():
    packed = ForgetCallback(confirm=True).pack()
    assert len(packed.encode()) <= 64  # Telegram's callback_data limit
    assert ForgetCallback.unpack(packed).confirm is True


def test_settings_without_token(monkeypatch):
    monkeypatch.delenv("RECEPTIONIST_BOT_TOKEN", raising=False)
    assert Settings(_env_file=None).bot_token is None
    assert "@BotFather" in NO_TOKEN
