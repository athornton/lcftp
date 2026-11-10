from urllib.parse import parse_qs, urlparse

import httpx
import respx

from lcftp_auth.github import GitHubClient


async def _login(client: httpx.AsyncClient) -> httpx.Response:
    r = await client.get("/auth/login")
    assert r.status_code == 307
    location = urlparse(r.headers["location"])
    assert location.netloc == "github.com"
    query = parse_qs(location.query)
    assert query["client_id"] == ["cid"]
    assert query["scope"] == ["read:org"]
    assert query["redirect_uri"] == ["https://lcftp.example.com/auth/callback"]
    state = query["state"][0]
    return await client.get(
        "/auth/callback", params={"code": "abc", "state": state}
    )


async def test_member_logs_in(client: httpx.AsyncClient) -> None:
    r = await _login(client)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    r = await client.get("/me")
    assert r.status_code == 200
    assert r.json() == {"login": "athornton"}


async def test_non_member_is_refused(
    client: httpx.AsyncClient, github: respx.MockRouter
) -> None:
    github.get("https://api.github.com/user/memberships/orgs/lcftp").respond(
        404, json={"message": "Not Found"}
    )
    r = await _login(client)
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    assert r.json()["message"] == (
        "You must be a member of the lcftp GitHub organization."
    )
    assert (await client.get("/me")).status_code == 401


async def test_pending_member_is_refused(
    client: httpx.AsyncClient, github: respx.MockRouter
) -> None:
    github.get("https://api.github.com/user/memberships/orgs/lcftp").respond(
        json={"state": "pending"}
    )
    assert (await _login(client)).status_code == 403


async def test_wrong_state_is_rejected(client: httpx.AsyncClient) -> None:
    await client.get("/auth/login")
    r = await client.get("/auth/callback", params={"code": "a", "state": "x"})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_request"


async def test_unauthenticated_request(client: httpx.AsyncClient) -> None:
    r = await client.get("/me")
    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


async def test_logout_clears_session(client: httpx.AsyncClient) -> None:
    await _login(client)
    r = await client.post("/auth/logout")
    assert r.status_code == 303
    assert (await client.get("/me")).status_code == 401


async def test_github_token_error(
    client: httpx.AsyncClient, github: respx.MockRouter
) -> None:
    github.post("https://github.com/login/oauth/access_token").respond(
        json={"error": "bad_verification_code"}
    )
    r = await _login(client)
    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


async def test_github_client_membership(
    http: httpx.AsyncClient, github: respx.MockRouter
) -> None:
    gh = GitHubClient(http)
    assert await gh.is_active_member("tok", "lcftp")
    assert await gh.get_user("tok") == ("athornton", 4242)
    request = github.calls.last.request
    assert request.headers["authorization"] == "Bearer tok"
