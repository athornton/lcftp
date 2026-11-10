"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings

__all__ = ["SessionDep", "SettingsDep", "get_session", "get_settings"]


def get_settings(request: Request) -> Settings:
    """Return the application settings."""
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Provide a database session wrapped in a transaction."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with factory() as session, session.begin():
        yield session


type SessionDep = Annotated[AsyncSession, Depends(get_session)]
type SettingsDep = Annotated[Settings, Depends(get_settings)]
