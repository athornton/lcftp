"""Storage protocols the server implements for authentication."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict

__all__ = ["User", "UserStore"]


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
