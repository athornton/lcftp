from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lcftp.config import Settings
from lcftp.main import create_app
from lcftp_models.errors import ErrorCode, LcftpError


async def test_health(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_ready(client: AsyncClient) -> None:
    r = await client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"database": "ok", "candidates_as_of": None}


async def test_ready_reports_database_failure(settings: Settings) -> None:
    broken = settings.model_copy(
        update={"database_url": "postgresql+asyncpg://x@127.0.0.1:1/x"}
    )
    app = create_app(broken)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["code"] == "internal"


async def test_lcftp_error_rendering(
    app: FastAPI, client: AsyncClient
) -> None:
    @app.get("/boom")
    async def boom() -> None:
        raise LcftpError(ErrorCode.not_found, "x", 404)

    r = await client.get("/boom")
    assert r.status_code == 404
    assert r.json() == {"code": "not_found", "message": "x", "detail": None}
