"""Database-backed device token store."""

from datetime import UTC, datetime

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lcftp_auth.stores import DeviceToken

from ..db.schema import DeviceTokenRow, ObservatoryRow, UserRow

__all__ = ["SqlTokenStore"]


def _query() -> Select[DeviceTokenRow, str]:
    return (
        select(DeviceTokenRow, UserRow.github_login)
        .join(
            ObservatoryRow, ObservatoryRow.id == DeviceTokenRow.observatory_id
        )
        .join(UserRow, UserRow.id == ObservatoryRow.owner_id)
    )


def _to_token(row: DeviceTokenRow, owner: str) -> DeviceToken:
    return DeviceToken(
        id=row.id,
        observatory_id=row.observatory_id,
        owner_login=owner,
        label=row.label,
        created_at=row.created_at,
        last_used=row.last_used,
        revoked_at=row.revoked_at,
    )


class SqlTokenStore:
    """`~lcftp_auth.stores.TokenStore` backed by PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, observatory_id: str, label: str, token_hash: str
    ) -> DeviceToken:
        """Record a new token for an observatory."""
        row = DeviceTokenRow(
            observatory_id=observatory_id,
            label=label,
            token_hash=token_hash,
            created_at=datetime.now(tz=UTC),
            last_used=None,
            revoked_at=None,
        )
        self._session.add(row)
        await self._session.flush()
        token = await self._get(row.id)
        if token is None:  # pragma: no cover
            msg = f"Token {row.id} vanished after creation"
            raise RuntimeError(msg)
        return token

    async def lookup(self, token_hash: str) -> DeviceToken | None:
        """Find a token by hash, including revoked tokens."""
        stmt = _query().where(DeviceTokenRow.token_hash == token_hash)
        result = (await self._session.execute(stmt)).one_or_none()
        return _to_token(result[0], result[1]) if result else None

    async def touch(self, token_id: int, when: datetime) -> None:
        """Record that a token was used."""
        await self._update(token_id, last_used=when)

    async def revoke(self, token_id: int, when: datetime) -> None:
        """Revoke a token."""
        await self._update(token_id, revoked_at=when)

    async def list_for(self, observatory_id: str) -> list[DeviceToken]:
        """List an observatory's tokens, oldest first."""
        stmt = (
            _query()
            .where(DeviceTokenRow.observatory_id == observatory_id)
            .order_by(DeviceTokenRow.id)
        )
        return [
            _to_token(*r) for r in (await self._session.execute(stmt)).all()
        ]

    async def _get(self, token_id: int) -> DeviceToken | None:
        stmt = _query().where(DeviceTokenRow.id == token_id)
        result = (await self._session.execute(stmt)).one_or_none()
        return _to_token(result[0], result[1]) if result else None

    async def _update(self, token_id: int, **values: datetime) -> None:
        stmt = (
            update(DeviceTokenRow)
            .where(DeviceTokenRow.id == token_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await self._session.execute(stmt)
        self._session.expire_all()
