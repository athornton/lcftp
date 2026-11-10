import hashlib
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from lcftp.storage.memory import MemoryObjectStore
from lcftp_models.checksums import compute_checksums


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


async def _put(
    store: MemoryObjectStore, url: str, data: bytes, headers: dict[str, str]
) -> httpx.Response:
    transport = httpx.ASGITransport(app=store.asgi_app)
    async with httpx.AsyncClient(transport=transport) as client:
        return await client.put(url, content=data, headers=headers)


async def test_missing_object(store: MemoryObjectStore) -> None:
    assert await store.stat("nope") is None


async def test_signed_put(store: MemoryObjectStore, tmp_path: Path) -> None:
    path = tmp_path / "f.fits"
    path.write_bytes(b"hello\n")
    sums = compute_checksums(path)
    signed = await store.sign_upload("a/b.fits", 6, sums, timedelta(hours=1))
    r = await _put(store, signed.url, b"hello\n", signed.headers)
    assert r.status_code == 200
    stat = await store.stat("a/b.fits")
    assert stat is not None
    assert stat.size_bytes == 6
    assert stat.sha256 == sums.sha256
    assert stat.md5_b64 == sums.md5_b64
    assert stat.crc32c_b64 == sums.crc32c_b64


async def test_checksum_mismatch_rejected(
    store: MemoryObjectStore, tmp_path: Path
) -> None:
    path = tmp_path / "f.fits"
    path.write_bytes(b"hello\n")
    sums = compute_checksums(path)
    signed = await store.sign_upload("a/b.fits", 6, sums, timedelta(hours=1))
    r = await _put(store, signed.url, b"HELLO\n", signed.headers)
    assert r.status_code == 400
    assert await store.stat("a/b.fits") is None


async def test_expired_url_rejected(
    store: MemoryObjectStore, tmp_path: Path
) -> None:
    path = tmp_path / "f.fits"
    path.write_bytes(b"x")
    sums = compute_checksums(path)
    signed = await store.sign_upload("k", 1, sums, timedelta(seconds=-1))
    r = await _put(store, signed.url, b"x", signed.headers)
    assert r.status_code == 403


def test_put_hook(store: MemoryObjectStore) -> None:
    store.put("k", b"abc")
    assert store.objects["k"] == b"abc"
    assert hashlib.sha256(b"abc").hexdigest() == store.sha256_of("k")
