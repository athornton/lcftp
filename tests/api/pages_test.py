from httpx import AsyncClient

from tests.support.auth import login

FORM = {
    "name": "Backyard",
    "latitude": "32.23",
    "longitude": "-110.95",
    "elevation_m": "728",
    "timezone": "America/Phoenix",
    "aperture_m": "0.05",
    "limiting_mag": "15",
    "fov_arcmin": "77",
    "driver": "simulated",
}


async def test_index_anonymous(client: AsyncClient) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    assert 'href="/auth/login"' in r.text


async def test_register_via_form(client: AsyncClient) -> None:
    await login(client)
    r = await client.get("/")
    assert "athornton" in r.text
    assert (await client.get("/observatories/new")).status_code == 200

    r = await client.post("/observatories/new", data=FORM)
    assert r.status_code == 303
    detail_url = r.headers["location"]
    assert detail_url.startswith("/observatories/")

    r = await client.get(detail_url)
    assert r.status_code == 200
    assert "Backyard" in r.text
    assert "Backyard" in (await client.get("/")).text


async def test_create_token_via_form(client: AsyncClient) -> None:
    await login(client)
    detail_url = (await client.post("/observatories/new", data=FORM)).headers[
        "location"
    ]
    r = await client.post(f"{detail_url}/tokens", data={"label": "pi"})
    assert r.status_code == 200
    assert "lcftp_" in r.text
    r = await client.get(detail_url)
    assert "pi" in r.text
    assert "lcftp_" not in r.text


async def test_form_validation_error(client: AsyncClient) -> None:
    await login(client)
    r = await client.post(
        "/observatories/new", data={**FORM, "limiting_mag": "99"}
    )
    assert r.status_code == 422
    assert "limiting_mag" in r.text


async def test_pages_require_login(client: AsyncClient) -> None:
    r = await client.get("/observatories/new")
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login"
