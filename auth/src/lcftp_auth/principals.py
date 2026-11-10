"""Authenticated identities."""

from pydantic import BaseModel, ConfigDict

__all__ = ["DevicePrincipal", "UserPrincipal"]


class UserPrincipal(BaseModel):
    """A logged-in user (browser session)."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    github_login: str


class DevicePrincipal(BaseModel):
    """An observatory client authenticated by device token."""

    model_config = ConfigDict(frozen=True)

    token_id: int
    observatory_id: str
    owner_login: str
