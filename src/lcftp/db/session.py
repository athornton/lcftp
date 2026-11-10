"""Engine and session factories."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import Settings

__all__ = ["create_engine", "create_sessionmaker"]


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine for the configured database."""
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def create_sessionmaker(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to an engine."""
    return async_sessionmaker(engine, expire_on_commit=False)
