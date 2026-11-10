"""Log test clients in through a mocked GitHub."""

import respx
from httpx import AsyncClient

__all__ = ["login"]


async def login(
    client: AsyncClient, github_login: str = "athornton", github_id: int = 4242
) -> None:
    """Log ``client`` in as a member of the ``lcftp`` organization."""
    with respx.mock(assert_all_called=False) as github:
        github.post("https://github.com/login/oauth/access_token").respond(
            json={"access_token": f"tok-{github_id}"}
        )
        github.get("https://api.github.com/user").respond(
            json={"login": github_login, "id": github_id}
        )
        github.get(
            "https://api.github.com/user/memberships/orgs/lcftp"
        ).respond(json={"state": "active"})
        r = await client.get("/auth/login")
        state = r.headers["location"].split("state=")[1].split("&")[0]
        r = await client.get(
            "/auth/callback", params={"code": "c", "state": state}
        )
        assert r.status_code == 303, r.text
