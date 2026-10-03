"""Chat with the receptionist in a terminal: real Claude and database, no Telegram.

    uv run python -m receptionist.console [--lang ru|uz|en] [--new] [message ...]

With messages as arguments it sends them in order and exits (a scripted run);
without, it reads lines until an empty one. Every turn costs real API money.
"""

import argparse
import asyncio
import logging
import sys

from receptionist.app import LoggingNotifier, build_assistant
from receptionist.config import Settings
from receptionist.core.clinic import load_clinic
from receptionist.db.conversations import SqlChatRepository
from receptionist.db.session import create_engine, create_session_factory
from receptionist.llm import make_client

# Telegram chat ids are non-zero, so 0 can never collide with a real chat.
CONSOLE_CHAT_ID = 0


async def run(messages: list[str], language: str, new_session: bool) -> None:
    settings = Settings()
    client = make_client(settings)
    if client is None:
        raise SystemExit("Set RECEPTIONIST_ANTHROPIC_API_KEY in .env.")
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    try:
        chat = await SqlChatRepository(sessions).ensure(CONSOLE_CHAT_ID, language)
        assistant = build_assistant(
            settings, client, load_clinic(settings.clinic_file), sessions, LoggingNotifier()
        )
        if new_session:
            await assistant.reset(chat.id)
        total = 0.0
        for line in messages or _stdin_lines():
            print(f"\n> {line}")
            reply = await assistant.reply(chat.id, line, language)
            total += reply.cost_usd
            u = reply.usage
            print(reply.text)
            print(
                f"  [{reply.outcome}{', handoff' if reply.handoff else ''} | "
                f"in {u['input_tokens']} cache_read {u['cache_read_input_tokens']} "
                f"out {u['output_tokens']} | ${reply.cost_usd:.4f}]"
            )
        print(f"\nTotal: ${total:.4f}")
    finally:
        await client.close()
        await engine.dispose()


def _stdin_lines():
    while line := input("> ").strip():
        yield line


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", choices=["ru", "uz", "en"], default="ru")
    parser.add_argument("--new", action="store_true", help="start a new session")
    parser.add_argument("messages", nargs="*")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="  %(levelname)s %(name)s - %(message)s")
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    asyncio.run(run(args.messages, args.lang, args.new))


if __name__ == "__main__":
    main()
