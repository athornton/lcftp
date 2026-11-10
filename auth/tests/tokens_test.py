from datetime import UTC, datetime
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import HttpUrl, SecretStr

from lcftp_auth.auth import build_auth
from lcftp_auth.config import AuthConfig
from lcftp_auth.principals import DevicePrincipal
from lcftp_auth.stores import DeviceToken
from lcftp_auth.tokens import generate_token, hash_token
from lcftp_models.errors import LcftpError


class MemoryTokenStore:
    def __init__(self) -> None:
        self.tokens: dict[int, DeviceToken] = {}
        self.hashes: dict[str, int] = {}

    async def create(
        self, observatory_id: str, label: str, token_hash: str
    ) -> DeviceToken:
        token = DeviceToken(
            id=len(self.tokens) + 1,
            observatory_id=observatory_id,
            owner_login="athornton",
            label=label,
            created_at=datetime.now(tz=UTC),
            last_used=None,
            revoked_at=None,
        )
        self.tokens[token.id] = token
        self.hashes[token_hash] = token.id
        return token

    async def lookup(self, token_hash: str) -> DeviceToken | None:
        token_id = self.hashes.get(token_hash)
        return self.tokens[token_id] if token_id else None

    async def touch(self, token_id: int, when: datetime) -> None:
        self.tokens[token_id] = self.tokens[token_id].model_copy(
            update={"last_used": when}
        )

    async def revoke(self, token_id: int, when: datetime) -> None:
        self.tokens[token_id] = self.tokens[token_id].model_copy(
            update={"revoked_at": when}
        )

    async def list_for(self, observatory_id: str) -> list[DeviceToken]:
        return [
            t
            for t in self.tokens.values()
            if t.observatory_id == observatory_id
        ]


@pytest.fixture
def tokens() -> MemoryTokenStore:
    return MemoryTokenStore()


@pytest.fixture
def app(tokens: MemoryTokenStore) -> FastAPI:
    config = AuthConfig(
        github_client_id="cid",
        github_client_secret=SecretStr("x"),
        github_org="lcftp",
        base_url=HttpUrl("https://lcftp.example.com"),
        session_secret=SecretStr("s" * 32),
    )
    auth = build_auth(
        config,
        user_store=lambda: None,
        token_store=lambda: tokens,
        http=lambda: None,
    )
    app = FastAPI()

    @app.exception_handler(LcftpError)
    async def handler(request: Request, exc: LcftpError) -> JSONResponse:
        body = exc.to_response().model_dump(mode="json")
        return JSONResponse(body, status_code=exc.status)

    @app.get("/device")
    async def device(
        principal: Annotated[DevicePrincipal, Depends(auth.require_device)],
    ) -> dict[str, str]:
        return {"observatory_id": principal.observatory_id}

    return app


async def _get(app: FastAPI, headers: dict[str, str]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://t"
    ) as c:
        return await c.get("/device", headers=headers)


def test_generated_tokens() -> None:
    a, b = generate_token(), generate_token()
    assert a != b
    assert a.startswith("lcftp_")
    assert len(a) > 40


def test_hash_token() -> None:
    token = generate_token()
    assert hash_token(token) == hash_token(token)
    assert hash_token(token) != token
    assert len(hash_token(token)) == 64


async def test_valid_token(app: FastAPI, tokens: MemoryTokenStore) -> None:
    token = generate_token()
    created = await tokens.create("obs1", "pi", hash_token(token))
    r = await _get(app, {"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"observatory_id": "obs1"}
    assert tokens.tokens[created.id].last_used is not None


async def test_revoked_token(app: FastAPI, tokens: MemoryTokenStore) -> None:
    token = generate_token()
    created = await tokens.create("obs1", "pi", hash_token(token))
    await tokens.revoke(created.id, datetime.now(tz=UTC))
    r = await _get(app, {"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert (
        r.json()["message"] == "Device token is invalid or has been revoked."
    )


@pytest.mark.parametrize(
    "header", [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"}]
)
async def test_malformed_header(app: FastAPI, header: dict[str, str]) -> None:
    r = await _get(app, header)
    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


async def test_unknown_token(app: FastAPI) -> None:
    r = await _get(app, {"Authorization": f"Bearer {generate_token()}"})
    assert r.status_code == 401
