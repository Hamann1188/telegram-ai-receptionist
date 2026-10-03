import logging

from aiogram import Bot, Dispatcher

from receptionist.config import Settings
from receptionist.telegram import handlers

logger = logging.getLogger(__name__)

NO_TOKEN = "Set RECEPTIONIST_BOT_TOKEN in .env (get a token from @BotFather)."


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(handlers.router)
    return dispatcher


async def run_polling(settings: Settings) -> None:
    """Long polling: no public URL or TLS certificate needed (ADR-4)."""
    if settings.bot_token is None:
        raise SystemExit(NO_TOKEN)
    bot = Bot(settings.bot_token.get_secret_value())
    dispatcher = build_dispatcher()
    # Polling and a webhook can't coexist; clear one left over from another deployment.
    await bot.delete_webhook(drop_pending_updates=False)
    logger.info("Starting long polling")
    await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
