"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lcftp_auth.auth import Auth
from lcftp_auth.principals import DevicePrincipal, UserPrincipal

from ..config import Settings
from ..stores.tokens import SqlTokenStore
from ..stores.users import SqlUserStore

__all__ = [
    "DeviceDep",
    "SessionDep",
    "SettingsDep",
    "UserDep",
    "get_session",
    "get_settings",
    "require_device",
    "require_user",
]


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


async def require_user(request: Request, session: SessionDep) -> UserPrincipal:
    """Require a logged-in user."""
    auth: Auth = request.app.state.auth
    user: UserPrincipal = await auth.require_user(
        request, SqlUserStore(session)
    )
    return user


async def require_device(
    request: Request, session: SessionDep
) -> DevicePrincipal:
    """Require a valid device token."""
    auth: Auth = request.app.state.auth
    device: DevicePrincipal = await auth.require_device(
        request, SqlTokenStore(session)
    )
    return device


type UserDep = Annotated[UserPrincipal, Depends(require_user)]
type DeviceDep = Annotated[DevicePrincipal, Depends(require_device)]
