"""Storage protocols the server implements for authentication."""

from datetime import datetime
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict

__all__ = ["DeviceToken", "TokenStore", "User", "UserStore"]


class User(BaseModel):
    """A user known to LCFTP."""

    model_config = ConfigDict(frozen=True)

    id: int
    github_login: str
    github_id: int


class UserStore(Protocol):
    """Persistence for users."""

    async def upsert_github_user(self, login: str, github_id: int) -> User:
        """Create or update a user from their GitHub identity."""

    async def get(self, user_id: int) -> User | None:
        """Look up a user by id."""


class DeviceToken(BaseModel):
    """Metadata about a device token (never the token itself)."""

    model_config = ConfigDict(frozen=True)

    id: int
    observatory_id: str
    owner_login: str
    label: str
    created_at: AwareDatetime
    last_used: AwareDatetime | None
    revoked_at: AwareDatetime | None


class TokenStore(Protocol):
    """Persistence for device tokens."""

    async def create(
        self, observatory_id: str, label: str, token_hash: str
    ) -> DeviceToken:
        """Record a new token for an observatory."""

    async def lookup(self, token_hash: str) -> DeviceToken | None:
        """Find a token by hash, including revoked tokens."""

    async def touch(self, token_id: int, when: datetime) -> None:
        """Record that a token was used."""

    async def revoke(self, token_id: int, when: datetime) -> None:
        """Revoke a token."""

    async def list_for(self, observatory_id: str) -> list[DeviceToken]:
        """List an observatory's tokens."""
