"""Integration tests against a real PostgreSQL database.

They run only when RECEPTIONIST_TEST_DATABASE_URL is set; the database is created if
missing, migrated to head, and emptied before each test.
"""

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[2]
TABLES = "chats, sessions, messages, bookings, handoffs, relay_messages"


async def _ensure_database(url: str) -> None:
    parsed = make_url(url)
    conn = await asyncpg.connect(
        user=parsed.username,
        password=parsed.password,
        host=parsed.host,
        port=parsed.port,
        database="postgres",
    )
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", parsed.database):
            await conn.execute(f'CREATE DATABASE "{parsed.database}"')
    finally:
        await conn.close()


def run_sql(url: str, sql: str, **params) -> list:
    async def run() -> list:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(sql), params)
                return result.all() if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(run())


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("RECEPTIONIST_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set RECEPTIONIST_TEST_DATABASE_URL to run integration tests")
    asyncio.run(_ensure_database(url))
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(autouse=True)
def empty_database(database_url):
    run_sql(database_url, f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")


@pytest.fixture
def sql(database_url):
    """Run a statement in its own transaction and return the rows."""
    return lambda statement, **params: run_sql(database_url, statement, **params)
