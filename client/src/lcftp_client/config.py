"""Client configuration file."""

import os
import tomllib
from datetime import time
from pathlib import Path
from typing import Annotated

import tomli_w
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    SecretStr,
)

__all__ = ["ClientConfig", "default_config_path", "load_config", "save_config"]


class ClientConfig(BaseModel):
    """Configuration of an observatory client."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    server_url: HttpUrl
    token: SecretStr
    observatory_id: str
    spool_dir: Path
    driver: str = "simulated"
    driver_config: dict[str, JsonValue] = Field(default_factory=dict)
    nightly_start: time | None = None
    """Local time the observatory becomes available each night."""

    nightly_end: time | None = None
    """Local time the observatory stops being available each morning."""

    item_timeout_s: Annotated[float, Field(gt=0)] = 1800.0


def default_config_path() -> Path:
    """Return ``$LCFTP_CLIENT_CONFIG`` or ``~/.config/lcftp/client.toml``."""
    if path := os.environ.get("LCFTP_CLIENT_CONFIG"):
        return Path(path)
    return Path.home() / ".config" / "lcftp" / "client.toml"


def load_config(path: Path | None = None) -> ClientConfig:
    """Read the client configuration."""
    with (path or default_config_path()).open("rb") as f:
        return ClientConfig.model_validate(tomllib.load(f))


def save_config(config: ClientConfig, path: Path | None = None) -> None:
    """Write the configuration, readable only by its owner."""
    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json", exclude_none=True)
    data["token"] = config.token.get_secret_value()
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tomli_w.dumps(data))
    target.chmod(0o600)
