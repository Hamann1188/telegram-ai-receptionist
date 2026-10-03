"""aiogram handlers for patients' private chats. The logic lives in ChatService."""

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from receptionist.telegram.service import ChatService
from receptionist.telegram.texts import FORGET_BUTTONS, FORGET_KEPT, language_from_code

router = Router(name="patients")
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)


class ForgetCallback(CallbackData, prefix="forget"):
    confirm: bool


def _language_code(message: Message | CallbackQuery) -> str | None:
    return message.from_user.language_code if message.from_user else None


@router.message(CommandStart())
async def start(message: Message, service: ChatService) -> None:
    await message.answer(await service.start(message.chat.id, _language_code(message)))


@router.message(Command("forget"))
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


@router.callback_query(ForgetCallback.filter())
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


@router.message(F.text)
async def text(message: Message, service: ChatService, bot: Bot) -> None:
    # Telegram bots can't stream, so show "typing…" while the agent works.
    async with ChatActionSender.typing(chat_id=message.chat.id, bot=bot):
        replies = await service.text(message.chat.id, _language_code(message), message.text)
    for reply in replies:
        await message.answer(reply)


@router.message()
async def unsupported(message: Message, service: ChatService) -> None:
    await message.answer(service.unsupported(_language_code(message)))
