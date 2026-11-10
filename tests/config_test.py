from datetime import timedelta

import pytest

from lcftp.config import Settings


def test_defaults(settings: Settings) -> None:
    assert settings.github_org == "lcftp"
    assert settings.ingest_interval == timedelta(minutes=5)
    assert settings.ingest_mag_margin == 0.5
    assert settings.candidate_max_age == timedelta(days=7)
    assert settings.target_observatory_cap == 3
    assert settings.brokers == ["alerce"]
    assert settings.storage_prefix == ""
    assert settings.storage_region is None
    assert settings.max_frame_bytes == 200 * 1024**2
    assert settings.upload_url_ttl == timedelta(hours=1)


def test_storage_backend_defaults_to_s3(settings: Settings) -> None:
    assert Settings.model_fields["storage_backend"].default == "s3"


def test_overrides_from_env(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LCFTP_GITHUB_ORG", "other")
    monkeypatch.setenv("LCFTP_INGEST_INTERVAL", "60")
    monkeypatch.setenv("LCFTP_BROKERS", '["alerce", "fink"]')
    monkeypatch.setenv("LCFTP_STORAGE_PREFIX", "lcftp/")
    s = Settings()
    assert s.github_org == "other"
    assert s.ingest_interval == timedelta(seconds=60)
    assert s.brokers == ["alerce", "fink"]
    assert s.storage_prefix == "lcftp/"


def test_secrets_are_hidden(settings: Settings) -> None:
    assert "client-secret" not in repr(settings)
    assert "session-secret" not in repr(settings)
