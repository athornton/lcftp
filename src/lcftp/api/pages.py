"""Minimal server-rendered pages for registration and tokens."""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from lcftp_auth.principals import UserPrincipal
from lcftp_models.errors import LcftpError
from lcftp_models.observatory import ObservatoryCreate

from ..stores.observatories import SqlObservatoryStore
from ..stores.tokens import SqlTokenStore
from .deps import SessionDep, require_user
from .observatories import create_token, owned_observatory, revoke_token

__all__ = ["router"]

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(
    directory=Path(__file__).parent.parent / "templates"
)

_FORM_FIELDS = (
    "name",
    "latitude",
    "longitude",
    "elevation_m",
    "timezone",
    "aperture_m",
    "limiting_mag",
    "fov_arcmin",
    "driver",
)


async def _user(request: Request, session: SessionDep) -> UserPrincipal | None:
    try:
        return await require_user(request, session)
    except LcftpError:
        return None


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/auth/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: SessionDep) -> Response:
    """Show the login link or the user's observatories."""
    user = await _user(request, session)
    observatories = (
        await SqlObservatoryStore(session).list_for_owner(user.user_id)
        if user
        else []
    )
    return templates.TemplateResponse(
        request,
        "index.html",
        {"user": user, "observatories": observatories},
    )


@router.get("/observatories/new", response_class=HTMLResponse)
async def new_form(request: Request, session: SessionDep) -> Response:
    """Show the registration form."""
    user = await _user(request, session)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse(
        request, "observatory_new.html", {"user": user, "form": {}}
    )


@router.post("/observatories/new", response_class=HTMLResponse)
async def new_submit(request: Request, session: SessionDep) -> Response:
    """Register an observatory from the form."""
    user = await _user(request, session)
    if not user:
        return _login_redirect()
    form = {k: str(v) for k, v in (await request.form()).items()}
    data: dict[str, Any] = {k: form.get(k) for k in _FORM_FIELDS}
    data["site"] = {
        k: data.pop(k)
        for k in ("latitude", "longitude", "elevation_m", "timezone")
    }
    try:
        create = ObservatoryCreate.model_validate(data)
    except ValidationError as e:
        return templates.TemplateResponse(
            request,
            "observatory_new.html",
            {"user": user, "form": form, "errors": e.errors()},
            status_code=422,
        )
    store = SqlObservatoryStore(session)
    profile = await store.create(user.user_id, user.github_login, create)
    return RedirectResponse(f"/observatories/{profile.id}", status_code=303)


async def _detail(
    request: Request,
    session: SessionDep,
    observatory_id: str,
    new_token: object = None,
) -> Response:
    user = await _user(request, session)
    if not user:
        return _login_redirect()
    profile = await owned_observatory(observatory_id, user, session)
    tokens = await SqlTokenStore(session).list_for(profile.id)
    settings = request.app.state.settings
    return templates.TemplateResponse(
        request,
        "observatory_detail.html",
        {
            "user": user,
            "observatory": profile,
            "tokens": tokens,
            "new_token": new_token,
            "base_url": str(settings.base_url).rstrip("/"),
        },
    )


@router.get("/observatories/{observatory_id}", response_class=HTMLResponse)
async def detail(
    request: Request, session: SessionDep, observatory_id: str
) -> Response:
    """Show an observatory and its tokens."""
    return await _detail(request, session, observatory_id)


@router.post(
    "/observatories/{observatory_id}/tokens", response_class=HTMLResponse
)
async def new_token(
    request: Request,
    session: SessionDep,
    observatory_id: str,
    label: Annotated[str, Form(min_length=1, max_length=100)],
) -> Response:
    """Create a device token and show it once."""
    user = await _user(request, session)
    if not user:
        return _login_redirect()
    profile = await owned_observatory(observatory_id, user, session)
    token = await create_token(profile, label, session)
    return await _detail(request, session, observatory_id, token)


@router.post("/observatories/{observatory_id}/tokens/{token_id}/revoke")
async def revoke(
    request: Request, session: SessionDep, observatory_id: str, token_id: int
) -> Response:
    """Revoke a device token."""
    user = await _user(request, session)
    if not user:
        return _login_redirect()
    profile = await owned_observatory(observatory_id, user, session)
    await revoke_token(profile, token_id, session)
    return RedirectResponse(
        f"/observatories/{observatory_id}", status_code=303
    )
