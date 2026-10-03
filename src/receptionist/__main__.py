import asyncio
import logging

from aiogram import Bot

from receptionist.app import LoggingNotifier, build_assistant
from receptionist.config import Settings
from receptionist.core.clinic import load_clinic
from receptionist.db.conversations import SqlChatRepository, SqlRelayRepository
from receptionist.db.repositories import SqlBookingRepository, SqlHandoffRepository
from receptionist.db.session import create_engine, create_session_factory
from receptionist.llm import make_client
from receptionist.telegram.admin import AdminDesk
from receptionist.telegram.bot import NO_TOKEN, build_dispatcher, run_polling
from receptionist.telegram.ratelimit import RateLimiter
from receptionist.telegram.service import ChatService

logger = logging.getLogger("receptionist")


async def run(settings: Settings) -> None:
    if settings.bot_token is None:
        raise SystemExit(NO_TOKEN)
    client = make_client(settings)
    if client is None:
        raise SystemExit("Set RECEPTIONIST_ANTHROPIC_API_KEY in .env.")
    clinic = load_clinic(settings.clinic_file)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    bot = Bot(settings.bot_token.get_secret_value())
    chats = SqlChatRepository(sessions)

    desk = None
    notifier = LoggingNotifier()
    if settings.admin_chat_id is not None:
        desk = AdminDesk(
            bot,
            settings.admin_chat_id,
            settings.admin_language,
            clinic,
            chats,
            SqlRelayRepository(sessions),
            SqlHandoffRepository(sessions),
        )
        notifier = desk
    else:
        logger.warning("No RECEPTIONIST_ADMIN_CHAT_ID: notifications go to the log only")

    assistant = build_assistant(settings, client, clinic, sessions, notifier)
    if desk is not None:
        desk.attach(assistant)
    service = ChatService(
        assistant,
        chats,
        SqlBookingRepository(sessions),
        notifier,
        clinic,
        RateLimiter(settings.rate_limit_messages, settings.rate_limit_window_s),
        model=settings.model,
        desk=desk,
        max_message_chars=settings.max_message_chars,
        daily_budget_usd=settings.daily_budget_usd,
    )
    try:
        await run_polling(bot, build_dispatcher(service, desk, settings.admin_chat_id))
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
