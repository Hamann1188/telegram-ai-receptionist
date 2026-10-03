"""aiogram routers: patients' private chats, the admin group, and group setup.

The handlers only translate Telegram updates; the logic lives in ChatService and
AdminDesk. Routers are built by functions because an aiogram router can belong to
one dispatcher only.
"""

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)
from aiogram.utils.chat_action import ChatActionSender

from receptionist.telegram import admin_texts
from receptionist.telegram.admin import RETURN, TAKE_OVER, AdminCallback, AdminDesk
from receptionist.telegram.service import ChatService
from receptionist.telegram.texts import FORGET_BUTTONS, FORGET_KEPT, language_from_code


class ForgetCallback(CallbackData, prefix="forget"):
    confirm: bool


def display_name(user: User | None) -> str | None:
    if user is None:
        return None
    name = user.full_name + (f" @{user.username}" if user.username else "")
    return name[:200]


def _language_code(update: Message | CallbackQuery) -> str | None:
    return update.from_user.language_code if update.from_user else None


# --- patients ------------------------------------------------------------------


async def start(message: Message, service: ChatService) -> None:
    greeting = await service.start(
        message.chat.id, _language_code(message), display_name(message.from_user)
    )
    await message.answer(greeting)


async def forget(message: Message, service: ChatService) -> None:
    code = _language_code(message)
    question, ask = await service.forget_prompt(message.chat.id, code)
    markup = None
    if ask:
        yes, no = FORGET_BUTTONS[language_from_code(code)]
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=yes, callback_data=ForgetCallback(confirm=True).pack()
                    ),
                    InlineKeyboardButton(
                        text=no, callback_data=ForgetCallback(confirm=False).pack()
                    ),
                ]
            ]
        )
    await message.answer(question, reply_markup=markup)


async def forget_answer(
    query: CallbackQuery, callback_data: ForgetCallback, service: ChatService
) -> None:
    code = _language_code(query)
    if callback_data.confirm:
        text = await service.forget(query.message.chat.id, code)
    else:
        text = FORGET_KEPT[language_from_code(code)]
    await query.message.edit_text(text)
    await query.answer()


async def text(message: Message, service: ChatService, bot: Bot) -> None:
    # Telegram bots can't stream, so show "typing…" while the agent works.
    async with ChatActionSender.typing(chat_id=message.chat.id, bot=bot):
        replies = await service.text(
            message.chat.id, _language_code(message), message.text, display_name(message.from_user)
        )
    for reply in replies:
        await message.answer(reply)


async def media(message: Message, service: ChatService) -> None:
    replies = await service.media(message.chat.id, _language_code(message), message.message_id)
    for reply in replies:
        await message.answer(reply)


def patient_router() -> Router:
    router = Router(name="patients")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.message.register(start, CommandStart())
    router.message.register(forget, Command("forget"))
    router.callback_query.register(forget_answer, ForgetCallback.filter())
    router.message.register(text, F.text)
    router.message.register(media)
    return router


# --- admin group ---------------------------------------------------------------


async def admin_button(query: CallbackQuery, callback_data: AdminCallback, desk: AdminDesk) -> None:
    admin = query.from_user.full_name
    if callback_data.action == TAKE_OVER:
        changed, notice = await desk.take_over(callback_data.chat_id, admin)
    elif callback_data.action == RETURN:
        changed, notice = await desk.return_to_bot(callback_data.chat_id, admin)
    else:
        changed, notice = False, ""
    if changed:
        await query.message.edit_reply_markup(reply_markup=None)
    await query.answer(notice)


async def operator_reply(message: Message, desk: AdminDesk, bot: Bot) -> None:
    replied = message.reply_to_message
    if replied.from_user is None or replied.from_user.id != bot.id:
        return  # a reply to another staff member, not to the bot
    notice = await desk.operator_reply(replied.message_id, message.message_id)
    if notice:
        await message.reply(notice)


def admin_router(admin_chat_id: int) -> Router:
    router = Router(name="admin")
    router.message.filter(F.chat.id == admin_chat_id)
    router.callback_query.filter(F.message.chat.id == admin_chat_id)
    router.callback_query.register(admin_button, AdminCallback.filter())
    router.message.register(operator_reply, F.reply_to_message)
    return router


# --- any group: setup ----------------------------------------------------------


async def chat_id(message: Message) -> None:
    await message.reply(admin_texts.CHAT_ID.format(id=message.chat.id))


def setup_router() -> Router:
    router = Router(name="setup")
    router.message.filter(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    router.message.register(chat_id, Command("chatid"))
    return router
