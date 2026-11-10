"""Assemble the authentication router and dependencies."""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from lcftp_models.errors import ErrorCode, LcftpError

from .config import AuthConfig
from .github import AUTHORIZE_URL, GitHubClient
from .principals import DevicePrincipal, UserPrincipal
from .stores import TokenStore, UserStore
from .tokens import hash_token

__all__ = ["Auth", "build_auth"]

_USER_KEY = "user_id"
_STATE_KEY = "oauth_state"


@dataclass(frozen=True)
class Auth:
    """Authentication components for a FastAPI application."""

    router: APIRouter
    """Routes under ``/auth``: login, callback and logout."""

    require_user: Callable[..., Any]
    """Dependency returning the logged-in `UserPrincipal`."""

    require_device: Callable[..., Any]
    """Dependency returning the `DevicePrincipal` of a bearer token."""

    config: AuthConfig

    def session_middleware_kwargs(self) -> dict[str, Any]:
        """Arguments for Starlette's ``SessionMiddleware``."""
        return {
            "secret_key": self.config.session_secret.get_secret_value(),
            "same_site": "lax",
            "https_only": self.config.base_url.scheme == "https",
        }


def build_auth(
    config: AuthConfig,
    *,
    user_store: Callable[..., Any],
    token_store: Callable[..., Any],
    http: Callable[..., Any],
) -> Auth:
    """Build authentication for an application.

    Parameters
    ----------
    config
        Authentication settings.
    user_store
        FastAPI dependency returning a `UserStore`.
    token_store
        FastAPI dependency returning a ``TokenStore``.
    http
        FastAPI dependency returning an ``httpx.AsyncClient``.
    """
    users_dep = Annotated[UserStore, Depends(user_store)]
    tokens_dep = Annotated[TokenStore, Depends(token_store)]
    http_dep = Annotated[httpx.AsyncClient, Depends(http)]
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.get("/login")
    async def login(request: Request) -> RedirectResponse:
        state = secrets.token_urlsafe(32)
        request.session[_STATE_KEY] = state
        query = urlencode(
            {
                "client_id": config.github_client_id,
                "scope": "read:org",
                "state": state,
                "redirect_uri": config.callback_url,
            }
        )
        return RedirectResponse(f"{AUTHORIZE_URL}?{query}")

    @router.get("/callback")
    async def callback(
        request: Request,
        code: str,
        state: str,
        users: users_dep,
        client: http_dep,
    ) -> RedirectResponse:
        expected = request.session.pop(_STATE_KEY, None)
        if not expected or not secrets.compare_digest(expected, state):
            msg = "Invalid or expired login state"
            raise LcftpError(ErrorCode.invalid_request, msg, 400)
        github = GitHubClient(client)
        token = await github.exchange_code(code, config)
        if not await github.is_active_member(token, config.github_org):
            msg = (
                f"You must be a member of the {config.github_org}"
                " GitHub organization."
            )
            raise LcftpError(ErrorCode.forbidden, msg, 403)
        login, github_id = await github.get_user(token)
        user = await users.upsert_github_user(login, github_id)
        request.session[_USER_KEY] = user.id
        return RedirectResponse("/", status_code=303)

    @router.post("/logout")
    async def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    async def require_user(
        request: Request, users: users_dep
    ) -> UserPrincipal:
        user_id = request.session.get(_USER_KEY)
        user = await users.get(user_id) if isinstance(user_id, int) else None
        if user is None:
            msg = "You must log in"
            raise LcftpError(ErrorCode.not_authenticated, msg, 401)
        return UserPrincipal(user_id=user.id, github_login=user.github_login)

    async def require_device(
        request: Request, tokens: tokens_dep
    ) -> DevicePrincipal:
        scheme, _, token = request.headers.get("authorization", "").partition(
            " "
        )
        found = None
        if scheme.lower() == "bearer" and token.strip():
            found = await tokens.lookup(hash_token(token.strip()))
        if found is None or found.revoked_at is not None:
            msg = "Device token is invalid or has been revoked."
            raise LcftpError(ErrorCode.not_authenticated, msg, 401)
        await tokens.touch(found.id, datetime.now(tz=UTC))
        return DevicePrincipal(
            token_id=found.id,
            observatory_id=found.observatory_id,
            owner_login=found.owner_login,
        )

    return Auth(
        router=router,
        require_user=require_user,
        require_device=require_device,
        config=config,
    )
