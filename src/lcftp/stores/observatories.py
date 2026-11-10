"""Database-backed observatory store."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lcftp_models.observatory import ObservatoryCreate, ObservatoryProfile

from ..db.schema import ObservatoryRow

__all__ = ["SqlObservatoryStore"]


def _to_profile(row: ObservatoryRow) -> ObservatoryProfile:
    return ObservatoryProfile.model_validate(row.profile)


class SqlObservatoryStore:
    """Persistence for registered observatories."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, owner_id: int, owner_login: str, data: ObservatoryCreate
    ) -> ObservatoryProfile:
        """Register a new observatory."""
        profile = ObservatoryProfile(
            **data.model_dump(), id=uuid4().hex, owner=owner_login
        )
        now = datetime.now(tz=UTC)
        self._session.add(
            ObservatoryRow(
                id=profile.id,
                owner_id=owner_id,
                limiting_mag=profile.limiting_mag,
                profile=profile.model_dump(mode="json"),
                created_at=now,
                updated_at=now,
            )
        )
        await self._session.flush()
        return profile

    async def get(self, observatory_id: str) -> ObservatoryProfile | None:
        """Get an observatory by id."""
        row = await self._session.get(ObservatoryRow, observatory_id)
        return _to_profile(row) if row else None

    async def get_owned(
        self, observatory_id: str, owner_id: int
    ) -> ObservatoryProfile | None:
        """Get an observatory only if ``owner_id`` owns it."""
        row = await self._session.get(ObservatoryRow, observatory_id)
        return _to_profile(row) if row and row.owner_id == owner_id else None

    async def list_for_owner(self, owner_id: int) -> list[ObservatoryProfile]:
        """List a user's observatories, oldest first."""
        stmt = (
            select(ObservatoryRow)
            .where(ObservatoryRow.owner_id == owner_id)
            .order_by(ObservatoryRow.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [_to_profile(r) for r in rows]

    async def update(
        self, observatory_id: str, data: ObservatoryCreate
    ) -> ObservatoryProfile:
        """Replace an observatory's registration data."""
        row = await self._session.get(ObservatoryRow, observatory_id)
        if row is None:
            msg = f"Unknown observatory {observatory_id}"
            raise KeyError(msg)
        old = _to_profile(row)
        profile = ObservatoryProfile(
            **data.model_dump(), id=old.id, owner=old.owner
        )
        row.profile = profile.model_dump(mode="json")
        row.limiting_mag = profile.limiting_mag
        row.updated_at = datetime.now(tz=UTC)
        await self._session.flush()
        return profile

    async def faintest_limiting_mag(self) -> float | None:
        """Return the faintest limiting magnitude of any observatory."""
        stmt = select(func.max(ObservatoryRow.limiting_mag))
        return (await self._session.execute(stmt)).scalar_one_or_none()
