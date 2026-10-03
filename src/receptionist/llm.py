from anthropic import AsyncAnthropic

from receptionist.config import Settings


def make_client(settings: Settings) -> AsyncAnthropic | None:
    """The Claude client, or None when no API key is configured.

    `api_key` and `base_url` are always explicit: with them the SDK ignores
    ANTHROPIC_* environment variables, so a globally exported ANTHROPIC_BASE_URL
    (e.g. a local proxy) can't redirect the bot's traffic.
    """
    if settings.anthropic_api_key is None:
        return None
    return AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        base_url=settings.anthropic_base_url,
        timeout=settings.anthropic_timeout_s,
        max_retries=2,
    )
