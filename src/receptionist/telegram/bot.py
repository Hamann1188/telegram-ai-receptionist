import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from receptionist.telegram.handlers import admin_router, patient_router, setup_router
from receptionist.telegram.texts import COMMANDS

logger = logging.getLogger(__name__)

NO_TOKEN = "Set RECEPTIONIST_BOT_TOKEN in .env (get a token from @BotFather)."


def build_dispatcher(service=None, desk=None, admin_chat_id: int | None = None) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(patient_router())
    if admin_chat_id is not None:
        dispatcher.include_router(admin_router(admin_chat_id))
    dispatcher.include_router(setup_router())
    # Handlers receive these by parameter name (aiogram workflow data).
    dispatcher["service"] = service
    dispatcher["desk"] = desk
    return dispatcher


async def set_commands(bot: Bot) -> None:
    """The patients' command menu, in their app language where Telegram supports it."""
    scope = BotCommandScopeAllPrivateChats()
    for language, commands in COMMANDS.items():
        await bot.set_my_commands(
            [BotCommand(command=name, description=text) for name, text in commands.items()],
            scope=scope,
            language_code=None if language == "en" else language,
        )


async def run_polling(bot: Bot, dispatcher: Dispatcher) -> None:
    """Long polling: no public URL or TLS certificate needed (ADR-4)."""
    # Polling and a webhook can't coexist; clear one left over from another deployment.
    await bot.delete_webhook(drop_pending_updates=False)
    await set_commands(bot)
    logger.info("Starting long polling")
    await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
