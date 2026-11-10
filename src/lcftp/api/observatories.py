"""JSON API for observatory registration and device tokens."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from lcftp_auth.stores import DeviceToken
from lcftp_auth.tokens import generate_token, hash_token
from lcftp_models.errors import ErrorCode, LcftpError
from lcftp_models.observatory import ObservatoryCreate, ObservatoryProfile

from ..stores.observatories import SqlObservatoryStore
from ..stores.tokens import SqlTokenStore
from .deps import DeviceDep, SessionDep, UserDep

__all__ = ["NewToken", "TokenRequest", "owned_observatory", "router"]

router = APIRouter(prefix="/api", tags=["observatories"])


class TokenRequest(BaseModel):
    """Request to create a device token."""

    label: Annotated[str, Field(min_length=1, max_length=100)]


class NewToken(BaseModel):
    """A newly created device token; the token is shown only once."""

    token: str
    token_info: DeviceToken


async def owned_observatory(
    observatory_id: str, user: UserDep, session: SessionDep
) -> ObservatoryProfile:
    """Return the observatory if the logged-in user owns it, else 404."""
    store = SqlObservatoryStore(session)
    profile = await store.get_owned(observatory_id, user.user_id)
    if profile is None:
        msg = f"Observatory {observatory_id} not found"
        raise LcftpError(ErrorCode.not_found, msg, 404)
    return profile


type OwnedDep = Annotated[ObservatoryProfile, Depends(owned_observatory)]


@router.post("/observatories", status_code=201)
async def create_observatory(
    data: ObservatoryCreate, user: UserDep, session: SessionDep
) -> ObservatoryProfile:
    """Register an observatory."""
    store = SqlObservatoryStore(session)
    return await store.create(user.user_id, user.github_login, data)


@router.get("/observatories")
async def list_observatories(
    user: UserDep, session: SessionDep
) -> list[ObservatoryProfile]:
    """List the logged-in user's observatories."""
    return await SqlObservatoryStore(session).list_for_owner(user.user_id)


@router.get("/observatories/{observatory_id}")
async def get_observatory(profile: OwnedDep) -> ObservatoryProfile:
    """Get one of the user's observatories."""
    return profile


@router.put("/observatories/{observatory_id}")
async def update_observatory(
    data: ObservatoryCreate, profile: OwnedDep, session: SessionDep
) -> ObservatoryProfile:
    """Update one of the user's observatories."""
    return await SqlObservatoryStore(session).update(profile.id, data)


async def create_token(
    profile: ObservatoryProfile, label: str, session: SessionDep
) -> NewToken:
    """Create a device token for an observatory."""
    token = generate_token()
    info = await SqlTokenStore(session).create(
        profile.id, label, hash_token(token)
    )
    return NewToken(token=token, token_info=info)


@router.post("/observatories/{observatory_id}/tokens", status_code=201)
async def post_token(
    request: TokenRequest, profile: OwnedDep, session: SessionDep
) -> NewToken:
    """Create a device token; the token is returned only this once."""
    return await create_token(profile, request.label, session)


@router.get("/observatories/{observatory_id}/tokens")
async def list_tokens(
    profile: OwnedDep, session: SessionDep
) -> list[DeviceToken]:
    """List an observatory's device tokens (without secrets)."""
    return await SqlTokenStore(session).list_for(profile.id)


async def revoke_token(
    profile: ObservatoryProfile, token_id: int, session: SessionDep
) -> None:
    """Revoke one of an observatory's tokens, or raise 404."""
    store = SqlTokenStore(session)
    if token_id not in {t.id for t in await store.list_for(profile.id)}:
        msg = f"Token {token_id} not found"
        raise LcftpError(ErrorCode.not_found, msg, 404)
    await store.revoke(token_id, datetime.now(tz=UTC))


@router.delete(
    "/observatories/{observatory_id}/tokens/{token_id}", status_code=204
)
async def delete_token(
    token_id: int, profile: OwnedDep, session: SessionDep
) -> Response:
    """Revoke a device token."""
    await revoke_token(profile, token_id, session)
    return Response(status_code=204)


@router.get("/device/whoami")
async def whoami(device: DeviceDep, session: SessionDep) -> ObservatoryProfile:
    """Return the profile of the observatory a device token belongs to."""
    profile = await SqlObservatoryStore(session).get(device.observatory_id)
    if profile is None:  # pragma: no cover (tokens cascade-delete)
        msg = "Observatory not found"
        raise LcftpError(ErrorCode.not_found, msg, 404)
    return profile
