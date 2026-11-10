"""Helpers that insert rows needed by store and API tests."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from lcftp.db.schema import ObservatoryRow
from lcftp.stores.users import SqlUserStore
from lcftp_auth.stores import User
from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.site import SiteLocation

__all__ = ["TUCSON", "make_observatory", "make_user"]

TUCSON = SiteLocation(
    latitude=32.23,
    longitude=-110.95,
    elevation_m=728,
    timezone="America/Phoenix",
)


async def make_user(
    session: AsyncSession, login: str = "athornton", github_id: int = 4242
) -> User:
    """Create a user."""
    return await SqlUserStore(session).upsert_github_user(login, github_id)


async def make_observatory(
    session: AsyncSession,
    owner: User,
    observatory_id: str = "obs1",
    limiting_mag: float = 15.0,
    site: SiteLocation = TUCSON,
) -> ObservatoryProfile:
    """Insert an observatory row directly."""
    profile = ObservatoryProfile(
        id=observatory_id,
        owner=owner.github_login,
        name=f"Observatory {observatory_id}",
        site=site,
        aperture_m=0.05,
        limiting_mag=limiting_mag,
        fov_arcmin=77,
    )
    now = datetime.now(tz=UTC)
    session.add(
        ObservatoryRow(
            id=observatory_id,
            owner_id=owner.id,
            limiting_mag=limiting_mag,
            profile=profile.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()
    return profile
