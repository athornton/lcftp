from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from lcftp.stores.observatories import SqlObservatoryStore
from tests.support.auth import login

NEW: dict[str, Any] = {
    "name": "Backyard",
    "site": {
        "latitude": 32.23,
        "longitude": -110.95,
        "elevation_m": 728,
        "timezone": "America/Phoenix",
    },
    "aperture_m": 0.05,
    "limiting_mag": 15.0,
    "fov_arcmin": 77,
    "filters": [{"name": "r", "wavelength_nm": 620}],
}


async def test_create_list_get_update(client: AsyncClient) -> None:
    await login(client)
    r = await client.post("/api/observatories", json=NEW)
    assert r.status_code == 201
    created = r.json()
    assert created["owner"] == "athornton"
    assert created["driver"] == "simulated"
    obs_id = created["id"]

    r = await client.get("/api/observatories")
    assert [o["id"] for o in r.json()] == [obs_id]
    r = await client.get(f"/api/observatories/{obs_id}")
    assert r.json() == created

    r = await client.put(
        f"/api/observatories/{obs_id}", json={**NEW, "limiting_mag": 16.5}
    )
    assert r.status_code == 200
    assert r.json()["limiting_mag"] == 16.5
    assert r.json()["id"] == obs_id


async def test_requires_login(client: AsyncClient) -> None:
    r = await client.get("/api/observatories")
    assert r.status_code == 401


async def test_other_users_observatory_is_hidden(
    client: AsyncClient, other_client: AsyncClient
) -> None:
    await login(client)
    obs_id = (await client.post("/api/observatories", json=NEW)).json()["id"]
    await login(other_client, "someone", 99)
    for method in ("GET", "PUT"):
        r = await other_client.request(
            method, f"/api/observatories/{obs_id}", json=NEW
        )
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"
    r = await other_client.post(
        f"/api/observatories/{obs_id}/tokens", json={"label": "x"}
    )
    assert r.status_code == 404


async def test_token_lifecycle(client: AsyncClient) -> None:
    await login(client)
    obs_id = (await client.post("/api/observatories", json=NEW)).json()["id"]
    r = await client.post(
        f"/api/observatories/{obs_id}/tokens", json={"label": "pi"}
    )
    assert r.status_code == 201
    token = r.json()["token"]
    info = r.json()["token_info"]
    assert token.startswith("lcftp_")

    r = await client.get(f"/api/observatories/{obs_id}/tokens")
    assert r.json() == [info]
    assert token not in r.text

    headers = {"Authorization": f"Bearer {token}"}
    r = await client.get("/api/device/whoami", headers=headers)
    assert r.status_code == 200
    assert r.json()["id"] == obs_id

    r = await client.delete(f"/api/observatories/{obs_id}/tokens/{info['id']}")
    assert r.status_code == 204
    r = await client.get("/api/device/whoami", headers=headers)
    assert r.status_code == 401


async def test_create_validation_error(client: AsyncClient) -> None:
    await login(client)
    r = await client.post(
        "/api/observatories", json={**NEW, "limiting_mag": 99}
    )
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_request"


async def test_faintest_limiting_mag(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    store = SqlObservatoryStore(db_session)
    assert await store.faintest_limiting_mag() is None
    await login(client)
    await client.post("/api/observatories", json=NEW)
    await client.post("/api/observatories", json={**NEW, "limiting_mag": 19.0})
    assert await store.faintest_limiting_mag() == 19.0
