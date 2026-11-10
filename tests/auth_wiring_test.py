import respx
from httpx import AsyncClient


async def test_server_login_flow(client: AsyncClient) -> None:
    with respx.mock(assert_all_called=False) as github:
        github.post("https://github.com/login/oauth/access_token").respond(
            json={"access_token": "tok"}
        )
        github.get("https://api.github.com/user").respond(
            json={"login": "athornton", "id": 4242}
        )
        github.get(
            "https://api.github.com/user/memberships/orgs/lcftp"
        ).respond(json={"state": "active"})
        r = await client.get("/auth/login")
        state = r.headers["location"].split("state=")[1].split("&")[0]
        r = await client.get(
            "/auth/callback", params={"code": "c", "state": state}
        )
    assert r.status_code == 303
