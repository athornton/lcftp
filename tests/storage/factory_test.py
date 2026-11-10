import pytest

from lcftp.config import Settings
from lcftp.storage.factory import create_object_store
from lcftp.storage.memory import MemoryObjectStore
from lcftp.storage.s3 import S3ObjectStore


def test_memory(settings: Settings) -> None:
    assert isinstance(create_object_store(settings), MemoryObjectStore)


def test_s3(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    s3 = settings.model_copy(
        update={"storage_backend": "s3", "storage_region": "us-west-2"}
    )
    assert isinstance(create_object_store(s3), S3ObjectStore)
