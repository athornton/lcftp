"""Fixtures for lcftp-auth tests."""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Annotated

import httpx
import pytest
import pytest_asyncio
import respx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import HttpUrl, SecretStr
from starlette.middleware.sessions import SessionMiddleware

from lcftp_auth.auth import Auth, build_auth
from lcftp_auth.config import AuthConfig
from lcftp_auth.principals import UserPrincipal
from lcftp_auth.stores import User
from lcftp_models.errors import LcftpError

BASE_URL = "https://lcftp.example.com"


class MemoryUserStore:
    """In-memory `~lcftp_auth.stores.UserStore` for tests."""

    def __init__(self) -> None:
        self.users: dict[int, User] = {}

    async def upsert_github_user(self, login: str, github_id: int) -> User:
        for user in self.users.values():
            if user.github_id == github_id:
                updated = user.model_copy(update={"github_login": login})
                self.users[user.id] = updated
                return updated
        user = User(
            id=len(self.users) + 1, github_login=login, github_id=github_id
        )
        self.users[user.id] = user
        return user

    async def get(self, user_id: int) -> User | None:
        return self.users.get(user_id)


@pytest.fixture
def config() -> AuthConfig:
    return AuthConfig(
        github_client_id="cid",
        github_client_secret=SecretStr("csecret"),
        github_org="lcftp",
        base_url=HttpUrl(BASE_URL),
        session_secret=SecretStr("s" * 32),
    )


@pytest.fixture
def user_store() -> MemoryUserStore:
    return MemoryUserStore()


@pytest.fixture
def github() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.post("https://github.com/login/oauth/access_token").respond(
            json={"access_token": "gho_token", "token_type": "bearer"}
        )
        router.get("https://api.github.com/user").respond(
            json={"login": "athornton", "id": 4242}
        )
        router.get(
            "https://api.github.com/user/memberships/orgs/lcftp"
        ).respond(json={"state": "active", "role": "member"})
        yield router


@pytest_asyncio.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


def make_app(
    config: AuthConfig, users: MemoryUserStore, http: httpx.AsyncClient
) -> tuple[FastAPI, Auth]:
    auth = build_auth(
        config,
        user_store=lambda: users,
        token_store=lambda: None,
        http=lambda: http,
    )
    app = FastAPI()
    app.add_middleware(SessionMiddleware, **auth.session_middleware_kwargs())
    app.include_router(auth.router)

    @app.exception_handler(LcftpError)
    async def handler(request: Request, exc: LcftpError) -> JSONResponse:
        body = exc.to_response().model_dump(mode="json")
        return JSONResponse(body, status_code=exc.status)

    @app.get("/me")
    async def me(
        user: Annotated[UserPrincipal, Depends(auth.require_user)],
    ) -> dict[str, str]:
        return {"login": user.github_login}

    @app.get("/")
    async def index() -> dict[str, str]:
        return {"at": datetime.now(tz=UTC).isoformat()}

    return app, auth


@pytest_asyncio.fixture
async def client(
    config: AuthConfig,
    user_store: MemoryUserStore,
    http: httpx.AsyncClient,
    github: respx.MockRouter,
) -> AsyncIterator[httpx.AsyncClient]:
    app, _ = make_app(config, user_store, http)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as c:
        yield c
