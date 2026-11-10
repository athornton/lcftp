from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from lcftp.db.schema import Base

EXPECTED_TABLES = {
    "alembic_version",
    "assignments",
    "broker_state",
    "candidate_sources",
    "candidates",
    "device_tokens",
    "frames",
    "observatories",
    "outcomes",
    "plans",
    "upload_batches",
    "users",
}


async def test_migrations_create_expected_tables(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        tables = await conn.run_sync(
            lambda c: set(inspect(c).get_table_names())
        )
    assert tables == EXPECTED_TABLES


async def test_migrations_match_models(engine: AsyncEngine) -> None:
    def diff(conn: Connection) -> list[object]:
        return compare_metadata(
            MigrationContext.configure(conn), Base.metadata
        )

    async with engine.connect() as conn:
        assert await conn.run_sync(diff) == []
