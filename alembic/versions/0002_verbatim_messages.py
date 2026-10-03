"""verbatim message content (json, not jsonb) and a prompt fingerprint per session

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03

jsonb reorders object keys, so a tool_use `input` read back from jsonb isn't the
JSON the model produced. Replayed history must be byte-identical (prompt cache,
thinking-block binding), and `json` stores the text as given.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ALTER COLUMN content TYPE json USING content::text::json")
    # sha256 of model + system prompt + tools; a session is rotated when it changes.
    op.add_column("sessions", sa.Column("prompt_fingerprint", sa.String(64)))


def downgrade() -> None:
    op.drop_column("sessions", "prompt_fingerprint")
    op.execute("ALTER TABLE messages ALTER COLUMN content TYPE jsonb USING content::jsonb")
