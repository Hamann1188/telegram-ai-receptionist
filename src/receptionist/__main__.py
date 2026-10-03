import asyncio
import logging

from aiogram import Bot

from receptionist.app import LoggingNotifier, build_assistant
from receptionist.config import Settings
from receptionist.core.clinic import load_clinic
from receptionist.db.conversations import SqlChatRepository
from receptionist.db.repositories import SqlBookingRepository
from receptionist.db.session import create_engine, create_session_factory
from receptionist.llm import make_client
from receptionist.telegram.bot import NO_TOKEN, run_polling
from receptionist.telegram.ratelimit import RateLimiter
from receptionist.telegram.service import ChatService


async def run(settings: Settings) -> None:
    if settings.bot_token is None:
        raise SystemExit(NO_TOKEN)
    client = make_client(settings)
    if client is None:
        raise SystemExit("Set RECEPTIONIST_ANTHROPIC_API_KEY in .env.")
    clinic = load_clinic(settings.clinic_file)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    notifier = LoggingNotifier()
    service = ChatService(
        build_assistant(settings, client, clinic, sessions, notifier),
        SqlChatRepository(sessions),
        SqlBookingRepository(sessions),
        notifier,
        clinic,
        RateLimiter(settings.rate_limit_messages, settings.rate_limit_window_s),
        model=settings.model,
        max_message_chars=settings.max_message_chars,
        daily_budget_usd=settings.daily_budget_usd,
    )
    bot = Bot(settings.bot_token.get_secret_value())
    try:
        await run_polling(bot, service)
    finally:
        await client.close()
        await engine.dispose()


def main() -> None:
    # Logs carry ids, outcomes, token counts and costs, never message text.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
