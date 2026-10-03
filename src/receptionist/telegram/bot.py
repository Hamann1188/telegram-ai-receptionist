import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from receptionist.telegram import handlers
from receptionist.telegram.service import ChatService
from receptionist.telegram.texts import COMMANDS

logger = logging.getLogger(__name__)

NO_TOKEN = "Set RECEPTIONIST_BOT_TOKEN in .env (get a token from @BotFather)."


def build_dispatcher(service: ChatService | None = None) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(handlers.router)
    # Handlers receive the service by parameter name (aiogram workflow data).
    dispatcher["service"] = service
    return dispatcher


async def set_commands(bot: Bot) -> None:
    """The command menu, in the user's app language where Telegram supports it."""
    for language, commands in COMMANDS.items():
        await bot.set_my_commands(
            [BotCommand(command=name, description=text) for name, text in commands.items()],
            language_code=None if language == "en" else language,
        )


async def run_polling(bot: Bot, service: ChatService) -> None:
    """Long polling: no public URL or TLS certificate needed (ADR-4)."""
    dispatcher = build_dispatcher(service)
    # Polling and a webhook can't coexist; clear one left over from another deployment.
    await bot.delete_webhook(drop_pending_updates=False)
    await set_commands(bot)
    logger.info("Starting long polling")
    await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
