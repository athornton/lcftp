"""Database-backed user store."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lcftp_auth.stores import User

from ..db.schema import UserRow

__all__ = ["SqlUserStore"]


def _to_user(row: UserRow) -> User:
    return User(
        id=row.id, github_login=row.github_login, github_id=row.github_id
    )


class SqlUserStore:
    """`~lcftp_auth.stores.UserStore` backed by PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_github_user(self, login: str, github_id: int) -> User:
        """Create or update a user from their GitHub identity."""
        now = datetime.now(tz=UTC)
        stmt = (
            insert(UserRow)
            .values(
                github_login=login,
                github_id=github_id,
                created_at=now,
                last_login=now,
            )
            .on_conflict_do_update(
                index_elements=[UserRow.github_id],
                set_={"github_login": login, "last_login": now},
            )
            .returning(UserRow)
        )
        row = (await self._session.execute(stmt)).scalar_one()
        return _to_user(row)

    async def get(self, user_id: int) -> User | None:
        """Look up a user by id."""
        stmt = select(UserRow).where(UserRow.id == user_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_user(row) if row else None
