from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from RECEPTIONIST_* environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="RECEPTIONIST_",
        env_file=".env",
        extra="ignore",
        # `RECEPTIONIST_ADMIN_CHAT_ID=` in .env means "not set", not an invalid value.
        env_ignore_empty=True,
    )

    bot_token: SecretStr | None = None
    # Telegram group where operators get handoffs and booking notifications.
    admin_chat_id: int | None = None
    emergency_number: str = "103"  # ambulance in Uzbekistan
    # Relative to the working directory: the repo root in development, /app in Docker.
    clinic_file: Path = Path("data/clinic.yaml")

    anthropic_api_key: SecretStr | None = None
    # Always passed to the client explicitly, so a globally exported
    # ANTHROPIC_BASE_URL (e.g. a local proxy) is never picked up.
    anthropic_base_url: str = "https://api.anthropic.com"
    model: str = "claude-opus-5-5"

    database_url: str = "postgresql+asyncpg://receptionist:receptionist@localhost:5433/receptionist"
