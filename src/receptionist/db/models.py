"""Database tables. The Alembic migrations in alembic/versions are the source of truth;
a test checks that these models and the migrated schema agree."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = (
        CheckConstraint("mode IN ('bot', 'operator')", name="ck_chats_mode"),
        CheckConstraint("language IN ('ru', 'uz', 'en')", name="ck_chats_language"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tg_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    language: Mapped[str | None] = mapped_column(String(2))
    mode: Mapped[str] = mapped_column(String(16), server_default="bot")
    created_at: Mapped[datetime] = _created_at()


class Session(Base):
    """One conversation with Claude. A chat has at most one open session."""

    __tablename__ = "sessions"
    __table_args__ = (
        Index(
            "uq_sessions_one_open_per_chat",
            "chat_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = _created_at()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Summary carried into the next session when this one is rotated (ARCHITECTURE §4).
    summary: Mapped[str | None] = mapped_column(Text)
    # sha256 of model + system prompt + tools; a change starts a new session.
    prompt_fingerprint: Mapped[str | None] = mapped_column(String(64))


class Message(Base):
    """A message exactly as sent to or returned by the API (append-only)."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("session_id", "ordinal"),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    # Verbatim content blocks, thinking included. `json`, not `jsonb`: jsonb reorders
    # object keys, and replayed history must be byte-identical (ADR-10).
    content: Mapped[Any] = mapped_column(JSON)
    usage: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("status IN ('confirmed', 'cancelled')", name="ck_bookings_status"),
        CheckConstraint("slot_end > slot_start", name="ck_bookings_slot_order"),
        # No two confirmed bookings of one resource may overlap (ADR-8).
        ExcludeConstraint(
            ("resource", "="),
            (text("tstzrange(slot_start, slot_end)"), "&&"),
            name="ex_bookings_no_overlap",
            using="gist",
            where=text("status = 'confirmed'"),
        ),
        Index("ix_bookings_chat_id", "chat_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    service_id: Mapped[str] = mapped_column(String(64))
    resource: Mapped[str] = mapped_column(String(64))
    slot_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    slot_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    patient_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), server_default="confirmed")
    created_at: Mapped[datetime] = _created_at()
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Handoff(Base):
    __tablename__ = "handoffs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    admin_message_id: Mapped[int | None] = mapped_column(BigInteger)
    opened_at: Mapped[datetime] = _created_at()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
