from datetime import UTC, datetime

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lcftp.api.deps import SessionDep
from lcftp.db.schema import UserRow


async def test_commit_happens_before_response(app: FastAPI) -> None:
    now = datetime.now(tz=UTC)

    @app.post("/test/duplicate-users")
    async def duplicate(session: SessionDep) -> dict[str, str]:
        for login in ("a", "b"):
            session.add(
                UserRow(
                    github_login=login,
                    github_id=1,
                    created_at=now,
                    last_login=now,
                )
            )
        return {"status": "ok"}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/test/duplicate-users")
    assert r.status_code == 500
