"""Database helpers for server tests."""

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from lcftp.db.schema import Base

__all__ = ["migrate", "postgres_url", "truncate_all"]

ROOT = Path(__file__).parent.parent.parent


@contextmanager
def postgres_url() -> Iterator[str]:
    """Provide an asyncpg URL for an empty test database.

    Uses ``LCFTP_TEST_DATABASE_URL`` when set (for environments without
    Docker), otherwise starts PostgreSQL with testcontainers.
    """
    if url := os.environ.get("LCFTP_TEST_DATABASE_URL"):
        yield url
        return

    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415

    with PostgresContainer(
        "postgres:16-alpine",
        driver="asyncpg",
        dbname="lcftp",
        username="lcftp",
        password=os.urandom(16).hex(),
    ) as postgres:
        yield postgres.get_connection_url()


def migrate(url: str, *, downgrade_first: bool = True) -> None:
    """Bring the database schema to the latest Alembic revision."""
    env = {**os.environ, "LCFTP_DATABASE_URL": url}
    alembic = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(ROOT / "alembic.ini"),
    ]
    if downgrade_first:
        subprocess.run([*alembic, "downgrade", "base"], check=True, env=env)
    subprocess.run([*alembic, "upgrade", "head"], check=True, env=env)


async def truncate_all(engine: AsyncEngine) -> None:
    """Remove all rows from every application table."""
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
