"""chat display names and the admin-group relay map

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Telegram name and @username, so operators can tell patients apart.
    op.add_column("chats", sa.Column("display_name", sa.String(200)))
    # Which patient chat a bot message in the admin group belongs to: an operator's
    # reply to that message is delivered to the patient.
    op.create_table(
        "relay_messages",
        sa.Column("admin_chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("admin_message_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_relay_messages_chat_id", "relay_messages", ["chat_id"])


def downgrade() -> None:
    op.drop_table("relay_messages")
    op.drop_column("chats", "display_name")
