"""Configuration for authentication."""

from pydantic import BaseModel, ConfigDict, HttpUrl, SecretStr

__all__ = ["AuthConfig"]


class AuthConfig(BaseModel):
    """Settings needed by the GitHub login flow."""

    model_config = ConfigDict(frozen=True)

    github_client_id: str
    github_client_secret: SecretStr
    github_org: str
    base_url: HttpUrl
    session_secret: SecretStr

    @property
    def callback_url(self) -> str:
        """The OAuth redirect URI registered with GitHub."""
        return str(self.base_url).rstrip("/") + "/auth/callback"
