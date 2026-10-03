from types import SimpleNamespace

import pytest

from receptionist.config import Settings
from receptionist.telegram.bot import NO_TOKEN, build_dispatcher, run_polling
from receptionist.telegram.handlers import start
from receptionist.telegram.texts import GREETING, language_from_code


class FakeMessage:
    def __init__(self, language_code: str | None, has_user: bool = True):
        self.from_user = SimpleNamespace(language_code=language_code) if has_user else None
        self.sent: list[str] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.sent.append(text)


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


@pytest.mark.parametrize("code", ["ru", "uz", "en"])
async def test_start_greets_in_the_user_language(code):
    message = FakeMessage(code)
    await start(message)
    assert message.sent == [GREETING[code]]


async def test_start_without_user_falls_back_to_english():
    message = FakeMessage(None, has_user=False)
    await start(message)
    assert message.sent == [GREETING["en"]]


def test_greetings_say_the_clinic_is_fictional():
    markers = {"ru": "вымышленная", "uz": "o'ylab topilgan", "en": "fictional"}
    for language, text in GREETING.items():
        assert markers[language] in text


def test_dispatcher_handles_messages():
    assert build_dispatcher().resolve_used_update_types() == ["message"]


async def test_polling_needs_a_token(monkeypatch):
    monkeypatch.delenv("RECEPTIONIST_BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="RECEPTIONIST_BOT_TOKEN"):
        await run_polling(Settings(_env_file=None))
    assert "@BotFather" in NO_TOKEN
