"""Wiring shared by the Telegram bot and the console chat."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from receptionist.config import Settings
from receptionist.core.agent import AgentConfig, Assistant
from receptionist.core.clinic import Clinic
from receptionist.core.ports import BookingRecord, Notifier
from receptionist.core.prompts import render_system_prompt
from receptionist.core.tools import ToolContext
from receptionist.db.conversations import SqlConversationStore
from receptionist.db.repositories import SqlBookingRepository, SqlHandoffRepository

logger = logging.getLogger(__name__)


def build_assistant(
    settings: Settings,
    client,
    clinic: Clinic,
    sessions: async_sessionmaker[AsyncSession],
    notifier: Notifier,
) -> Assistant:
    bookings = SqlBookingRepository(sessions)
    handoffs = SqlHandoffRepository(sessions)

    def tool_context(chat_id: int) -> ToolContext:
        return ToolContext(chat_id, clinic, bookings, handoffs, notifier)

    return Assistant(
        client,
        AgentConfig(model=settings.model, effort=settings.effort, max_tokens=settings.max_tokens),
        clinic,
        render_system_prompt(clinic, settings.emergency_number),
        SqlConversationStore(sessions),
        tool_context,
    )


class LoggingNotifier:
    """Admin notifications as log lines, until the admin group is wired (step 6)."""

    async def booking_created(self, booking: BookingRecord) -> None:
        logger.info(
            "Admin: booking #%s %s at %s", booking.id, booking.service_id, booking.slot_start
        )

    async def booking_cancelled(self, booking: BookingRecord) -> None:
        logger.info("Admin: booking #%s cancelled", booking.id)

    async def handoff_requested(
        self, chat_id: int, handoff_id: int, reason: str, summary: str
    ) -> None:
        logger.info("Admin: handoff #%s for chat %s (%s)", handoff_id, chat_id, reason)
