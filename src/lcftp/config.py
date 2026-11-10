"""Server configuration, read from ``LCFTP_*`` environment variables."""

from datetime import timedelta
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings"]


def _seconds(value: object) -> object:
    """Allow durations to be given as a plain number of seconds."""
    if isinstance(value, str) and value.strip().replace(".", "", 1).isdigit():
        return float(value)
    return value


type Duration = Annotated[timedelta, BeforeValidator(_seconds)]
"""A duration given as seconds or as an ISO 8601 duration."""


class Settings(BaseSettings):
    """LCFTP server settings."""

    model_config = SettingsConfigDict(env_prefix="LCFTP_", frozen=True)

    database_url: str = Field(
        ..., description="SQLAlchemy URL using the asyncpg driver"
    )
    github_client_id: str
    github_client_secret: SecretStr
    github_org: str = "lcftp"
    session_secret: SecretStr
    base_url: HttpUrl
    storage_backend: Literal["gcs", "s3", "memory"] = "s3"
    storage_bucket: str
    storage_prefix: str = Field(
        "", description="Prefix prepended to every object key, e.g. lcftp/"
    )
    storage_region: str | None = None
    storage_endpoint_url: HttpUrl | None = Field(
        None, description="Endpoint for S3-compatible stores such as MinIO"
    )
    brokers: list[str] = Field(default_factory=lambda: ["alerce"])
    ingest_interval: Duration = timedelta(minutes=5)
    ingest_mag_margin: float = 0.5
    ingest_lookback: Duration = Field(
        timedelta(hours=1),
        description="Overlap with the previous poll, to catch late alerts",
    )
    candidate_max_age: Duration = timedelta(days=7)
    target_observatory_cap: int = 3
    max_frame_bytes: int = 200 * 1024**2
    upload_url_ttl: Duration = timedelta(hours=1)
