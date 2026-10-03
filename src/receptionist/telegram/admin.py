"""The clinic's admin group: notifications, human handoff and the operator relay.

Every bot message in the group that concerns a patient is recorded in the relay map,
so an operator's reply to it reaches that patient. Operators reply only while the
chat is in operator mode; "Take over" and "Return to bot" switch modes.
"""

import contextlib
import logging
from collections.abc import Callable
from datetime import datetime

from aiogram.exceptions import TelegramAPIError
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReactionTypeEmoji,
    ReplyParameters,
)

from receptionist.core.clinic import Clinic
from receptionist.core.ports import BookingRecord
from receptionist.core.tools import utc_now
from receptionist.telegram import admin_texts as t
from receptionist.telegram.texts import BACK_TO_BOT, STAFF_JOINED, Language, language_from_code

logger = logging.getLogger(__name__)

TAKE_OVER = "take"
RETURN = "return"
STAFF_TAKEOVER_REASON = "staff_takeover"


class AdminCallback(CallbackData, prefix="admin"):
    action: str  # TAKE_OVER or RETURN
    chat_id: int


class AdminDesk:
    def __init__(
        self,
        bot,
        admin_chat_id: int,
        language: Language,
        clinic: Clinic,
        chats,
        relays,
        handoffs,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._bot = bot
        self._admin_chat_id = admin_chat_id
        self._lang = language
        self._clinic = clinic
        self._chats = chats
        self._relays = relays
        self._handoffs = handoffs
        self._clock = clock
        self._assistant = None

    def attach(self, assistant) -> None:
        """The assistant is built after the desk (it notifies through it)."""
        self._assistant = assistant

    # --- Notifier port (called by the booking and handoff tools) ---------------

    async def booking_created(self, booking: BookingRecord) -> None:
        await self._booking_card(t.BOOKING_CREATED, booking)

    async def booking_cancelled(self, booking: BookingRecord) -> None:
        await self._booking_card(t.BOOKING_CANCELLED, booking)

    async def handoff_requested(
        self, chat_id: int, handoff_id: int, reason: str, summary: str
    ) -> None:
        chat = await self._chats.get(chat_id)
        if chat is None:
            return
        reason_text = t.REASONS[self._lang].get(reason, reason)
        text = t.HANDOFF[self._lang].format(reason=reason_text, chat=chat.label, summary=summary)
        message_id = await self._post(chat.id, text, RETURN)
        await self._handoffs.set_admin_message(handoff_id, message_id)

    # --- patient side ----------------------------------------------------------

    async def forward_text(self, chat, text: str) -> bool:
        """A patient's message in operator mode; False if the group can't be reached."""
        message = t.PATIENT_MESSAGE[self._lang].format(chat=chat.label, text=text)
        try:
            await self._post(chat.id, message)
        except TelegramAPIError:
            logger.exception("Couldn't forward a patient message to the admin group")
            return False
        return True

    async def forward_file(self, chat, message_id: int) -> bool:
        """A photo, voice message or other file from a patient in operator mode."""
        try:
            header = await self._post(chat.id, t.PATIENT_FILE[self._lang].format(chat=chat.label))
            copied = await self._bot.copy_message(
                chat_id=self._admin_chat_id,
                from_chat_id=chat.tg_chat_id,
                message_id=message_id,
                reply_parameters=ReplyParameters(message_id=header),
            )
        except TelegramAPIError:
            logger.exception("Couldn't forward a patient file to the admin group")
            return False
        await self._relays.record(self._admin_chat_id, copied.message_id, chat.id)
        return True

    async def patient_restarted(self, chat) -> None:
        """The patient sent /start in operator mode, which ends it."""
        await self._post(chat.id, t.PATIENT_RESTARTED[self._lang].format(chat=chat.label))

    # --- admin side ------------------------------------------------------------

    async def operator_reply(self, reply_to_message_id: int, message_id: int) -> str | None:
        """Deliver an operator's reply to the patient. Returns a notice for the group,
        or None when the message was delivered."""
        chat_id = await self._relays.chat_for(self._admin_chat_id, reply_to_message_id)
        chat = await self._chats.get(chat_id) if chat_id is not None else None
        if chat is None:
            return t.UNKNOWN_CHAT[self._lang]
        if chat.mode != "operator":
            return t.NOT_OPERATOR_MODE[self._lang]
        try:
            await self._bot.copy_message(
                chat_id=chat.tg_chat_id, from_chat_id=self._admin_chat_id, message_id=message_id
            )
        except TelegramAPIError:
            logger.warning("Couldn't deliver an operator reply", exc_info=True)
            return t.DELIVERY_FAILED[self._lang]
        # Operators can reply to their own earlier messages too.
        await self._relays.record(self._admin_chat_id, message_id, chat.id)
        with contextlib.suppress(TelegramAPIError):  # the reaction is only a delivery receipt
            await self._bot.set_message_reaction(
                chat_id=self._admin_chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji="👍")],
            )
        return None

    async def take_over(self, chat_id: int, admin_name: str) -> tuple[bool, str]:
        """An operator takes the chat from the bot. Returns (changed, notice)."""
        chat = await self._chats.get(chat_id)
        if chat is None:
            return False, t.UNKNOWN_CHAT[self._lang]
        if not await self._chats.set_mode(chat.id, "operator", self._clock()):
            return False, t.ALREADY_OPERATOR[self._lang]
        handoff_id = await self._handoffs.open(
            chat.id, STAFF_TAKEOVER_REASON, f"Taken over by {admin_name}."
        )
        text = t.TAKEN_OVER[self._lang].format(admin=admin_name, chat=chat.label)
        await self._handoffs.set_admin_message(handoff_id, await self._post(chat.id, text, RETURN))
        await self._tell_patient(chat, STAFF_JOINED)
        return True, t.DONE[self._lang]

    async def return_to_bot(self, chat_id: int, admin_name: str) -> tuple[bool, str]:
        """The bot takes over again with a fresh conversation."""
        chat = await self._chats.get(chat_id)
        if chat is None:
            return False, t.UNKNOWN_CHAT[self._lang]
        if not await self._chats.set_mode(chat.id, "bot", self._clock()):
            return False, t.ALREADY_BOT[self._lang]
        if self._assistant is not None:
            await self._assistant.reset(chat.id)
        await self._tell_patient(chat, BACK_TO_BOT)
        await self._post(chat.id, t.RETURNED[self._lang].format(admin=admin_name, chat=chat.label))
        return True, t.DONE[self._lang]

    # --- helpers ---------------------------------------------------------------

    async def _post(self, chat_id: int, text: str, button: str | None = None) -> int:
        """Send to the admin group and remember which patient chat it is about."""
        markup = None
        if button is not None:
            label = (t.TAKE_OVER_BUTTON if button == TAKE_OVER else t.RETURN_BUTTON)[self._lang]
            data = AdminCallback(action=button, chat_id=chat_id).pack()
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data)]]
            )
        message = await self._bot.send_message(self._admin_chat_id, text, reply_markup=markup)
        await self._relays.record(self._admin_chat_id, message.message_id, chat_id)
        return message.message_id

    async def _booking_card(self, template: dict[Language, str], booking: BookingRecord) -> None:
        chat = await self._chats.get(booking.chat_id)
        if chat is None:
            return
        service = self._clinic.service(booking.service_id)
        start = booking.slot_start.astimezone(self._clinic.tz)
        end = booking.slot_end.astimezone(self._clinic.tz)
        text = template[self._lang].format(
            id=booking.id,
            service=getattr(service.name, self._lang) if service else booking.service_id,
            weekday=t.WEEKDAYS[self._lang][start.weekday()],
            date=f"{start:%d.%m.%Y}",
            start=f"{start:%H:%M}",
            end=f"{end:%H:%M}",
            name=booking.patient_name,
            phone=booking.phone,
            chat=chat.label,
        )
        await self._post(chat.id, text, TAKE_OVER if chat.mode == "bot" else None)

    async def _tell_patient(self, chat, texts: dict[Language, str]) -> None:
        try:
            await self._bot.send_message(chat.tg_chat_id, texts[language_from_code(chat.language)])
        except TelegramAPIError:
            logger.warning("Couldn't message the patient", exc_info=True)
