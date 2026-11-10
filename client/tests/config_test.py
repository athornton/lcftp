import stat
from datetime import time
from pathlib import Path

import pytest
from pydantic import HttpUrl, SecretStr

from lcftp_client.config import (
    ClientConfig,
    default_config_path,
    load_config,
    save_config,
)


def _config(tmp_path: Path) -> ClientConfig:
    return ClientConfig(
        server_url=HttpUrl("https://lcftp.example.com"),
        token=SecretStr("lcftp_secret"),
        observatory_id="obs1",
        spool_dir=tmp_path / "spool",
        driver_config={"seed": 1, "cloud_rate": 0.2},
        nightly_start=time(19, 0),
        nightly_end=time(5, 30),
    )


def test_round_trip(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = tmp_path / "client.toml"
    save_config(config, path)
    assert load_config(path) == config


def test_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "client.toml"
    save_config(_config(tmp_path), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_token_hidden_in_repr(tmp_path: Path) -> None:
    assert "lcftp_secret" not in repr(_config(tmp_path))


def test_default_path_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LCFTP_CLIENT_CONFIG", str(tmp_path / "x.toml"))
    assert default_config_path() == tmp_path / "x.toml"
    monkeypatch.delenv("LCFTP_CLIENT_CONFIG")
    assert (
        default_config_path()
        == Path.home() / ".config" / "lcftp" / "client.toml"
    )


def test_defaults(tmp_path: Path) -> None:
    config = ClientConfig(
        server_url=HttpUrl("https://x.example.com"),
        token=SecretStr("t"),
        observatory_id="o",
        spool_dir=tmp_path,
    )
    assert config.driver == "simulated"
    assert config.item_timeout_s == 1800.0
