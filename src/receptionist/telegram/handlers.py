from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from receptionist.telegram.texts import GREETING, language_from_code

router = Router(name="user")


@router.message(CommandStart())
async def start(message: Message) -> None:
    code = message.from_user.language_code if message.from_user else None
    await message.answer(GREETING[language_from_code(code)])
