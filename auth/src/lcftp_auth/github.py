"""Minimal GitHub OAuth and API client."""

import httpx

from lcftp_models.errors import ErrorCode, LcftpError

from .config import AuthConfig

__all__ = ["GitHubClient"]

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_URL = "https://api.github.com"


class GitHubClient:
    """Calls to GitHub needed for login and organization checks."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def exchange_code(self, code: str, config: AuthConfig) -> str:
        """Exchange an authorization code for an access token."""
        secret = config.github_client_secret.get_secret_value()
        r = await self._http.post(
            TOKEN_URL,
            data={
                "client_id": config.github_client_id,
                "client_secret": secret,
                "code": code,
                "redirect_uri": config.callback_url,
            },
            headers={"Accept": "application/json"},
        )
        token = r.json().get("access_token") if r.is_success else None
        if not isinstance(token, str):
            msg = "GitHub login failed"
            raise LcftpError(ErrorCode.not_authenticated, msg, 401)
        return token

    async def get_user(self, token: str) -> tuple[str, int]:
        """Return the login and numeric id of the token's user."""
        r = await self._http.get(
            f"{API_URL}/user", headers=self._headers(token)
        )
        r.raise_for_status()
        data = r.json()
        return str(data["login"]), int(data["id"])

    async def is_active_member(self, token: str, org: str) -> bool:
        """Whether the token's user is an active member of ``org``."""
        r = await self._http.get(
            f"{API_URL}/user/memberships/orgs/{org}",
            headers=self._headers(token),
        )
        return r.status_code == 200 and r.json().get("state") == "active"

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
