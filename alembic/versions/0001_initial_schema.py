"""chats, sessions, messages, bookings, handoffs

Revision ID: 0001
Revises:
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    # Needed for "resource WITH =" in the bookings exclusion constraint.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "chats",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tg_chat_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("language", sa.String(2)),
        sa.Column("mode", sa.String(16), server_default="bot", nullable=False),
        _created_at(),
        sa.CheckConstraint("mode IN ('bot', 'operator')", name="ck_chats_mode"),
        sa.CheckConstraint("language IN ('ru', 'uz', 'en')", name="ck_chats_language"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _created_at("started_at"),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("summary", sa.Text()),
    )
    op.create_index(
        "uq_sessions_one_open_per_chat",
        "sessions",
        ["chat_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "session_id",
            sa.BigInteger(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("usage", postgresql.JSONB()),
        _created_at(),
        sa.UniqueConstraint("session_id", "ordinal"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
    )

    op.create_table(
        "bookings",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("service_id", sa.String(64), nullable=False),
        sa.Column("resource", sa.String(64), nullable=False),
        sa.Column("slot_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slot_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("patient_name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default="confirmed", nullable=False),
        _created_at(),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('confirmed', 'cancelled')", name="ck_bookings_status"),
        sa.CheckConstraint("slot_end > slot_start", name="ck_bookings_slot_order"),
    )
    op.create_index("ix_bookings_chat_id", "bookings", ["chat_id"])
    op.execute(
        "ALTER TABLE bookings ADD CONSTRAINT ex_bookings_no_overlap "
        "EXCLUDE USING gist (resource WITH =, tstzrange(slot_start, slot_end) WITH &&) "
        "WHERE (status = 'confirmed')"
    )

    op.create_table(
        "handoffs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("admin_message_id", sa.BigInteger()),
        _created_at("opened_at"),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_handoffs_chat_id", "handoffs", ["chat_id"])


def downgrade() -> None:
    op.drop_table("handoffs")
    op.drop_table("bookings")
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_table("chats")
    # btree_gist is left installed: other objects in the database may use it.
