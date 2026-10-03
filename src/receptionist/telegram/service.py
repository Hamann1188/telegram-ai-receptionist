"""What the bot does with each patient update, independent of aiogram."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from receptionist.core.agent import usage_cost
from receptionist.core.clinic import Clinic
from receptionist.core.ports import BookingRepository, Notifier
from receptionist.core.prompts import fallback_text
from receptionist.core.tools import utc_now
from receptionist.telegram import texts
from receptionist.telegram.ratelimit import RateLimiter
from receptionist.telegram.texts import Language, detect_language, language_from_code

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(
        self,
        assistant,
        chats,
        bookings: BookingRepository,
        notifier: Notifier,
        clinic: Clinic,
        limiter: RateLimiter,
        *,
        model: str,
        max_message_chars: int = 2000,
        daily_budget_usd: float = 0.50,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._assistant = assistant
        self._chats = chats
        self._bookings = bookings
        self._notifier = notifier
        self._clinic = clinic
        self._limiter = limiter
        self._model = model
        self._max_chars = max_message_chars
        self._daily_budget = daily_budget_usd
        self._clock = clock

    async def start(self, tg_chat_id: int, language_code: str | None) -> str:
        """/start: a fresh conversation with the bot, also out of operator mode."""
        language = language_from_code(language_code)
        chat = await self._chats.ensure(tg_chat_id, language)
        if chat.mode != "bot":
            await self._chats.set_mode(chat.id, "bot")
        await self._assistant.reset(chat.id)
        return texts.GREETING[language]

    async def text(self, tg_chat_id: int, language_code: str | None, text: str) -> list[str]:
        """A patient's text message; returns the reply, split for Telegram."""
        language = detect_language(text, language_from_code(language_code))
        if not self._limiter.allow(tg_chat_id):
            return [texts.RATE_LIMITED[language]]
        if len(text) > self._max_chars:
            return [texts.TOO_LONG[language].format(limit=self._max_chars)]
        try:
            chat = await self._chats.ensure(tg_chat_id, language)
            if chat.mode == "operator":
                return [texts.OPERATOR_MODE[language]]
            if await self._spent_today(chat.id) >= self._daily_budget:
                logger.warning("Chat %s reached its daily budget", chat.id)
                return [texts.DAILY_LIMIT[language].format(phone=self._clinic.phone)]
            reply = await self._assistant.reply(chat.id, text, language)
        except Exception:
            logger.exception("Failed to handle a message")
            return [fallback_text("error", language, self._clinic)]
        return texts.split_message(reply.text)

    def unsupported(self, language_code: str | None) -> str:
        return texts.UNSUPPORTED[language_from_code(language_code)]

    async def forget_prompt(self, tg_chat_id: int, language_code: str | None) -> tuple[str, bool]:
        """/forget: the confirmation question, and whether to show the buttons."""
        language = language_from_code(language_code)
        chat = await self._chats.find(tg_chat_id)
        if chat is None:
            return texts.NOTHING_STORED[language], False
        question = texts.FORGET_CONFIRM[language]
        upcoming = await self._bookings.upcoming_for_chat(chat.id, self._clock())
        if upcoming:
            question += texts.FORGET_CANCELS_BOOKINGS[language].format(count=len(upcoming))
        return question, True

    async def forget(self, tg_chat_id: int, language_code: str | None) -> str:
        """Delete the chat's data after confirmation. Upcoming bookings are cancelled
        first, so the admins are told the slots are free again."""
        language: Language = language_from_code(language_code)
        chat = await self._chats.find(tg_chat_id)
        if chat is None:
            return texts.NOTHING_STORED[language]
        async with self._assistant.lock(chat.id):
            now = self._clock()
            for booking in await self._bookings.upcoming_for_chat(chat.id, now):
                cancelled = await self._bookings.cancel(chat.id, booking.id, now)
                if cancelled is not None:
                    try:
                        await self._notifier.booking_cancelled(cancelled)
                    except Exception:
                        logger.exception("Admin notification failed")
            await self._chats.forget(tg_chat_id)
        logger.info("Chat %s forgotten", chat.id)
        return texts.FORGET_DONE[language]

    async def _spent_today(self, chat_id: int) -> float:
        since = self._clock() - timedelta(hours=24)
        usages = await self._chats.usage_since(chat_id, since)
        return sum(usage_cost(self._model, usage) for usage in usages)
