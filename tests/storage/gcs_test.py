from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage
from google.oauth2 import service_account

from lcftp.storage.gcs import GcsObjectStore
from lcftp_models.checksums import compute_checksums


def _signing_credentials() -> service_account.Credentials:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return service_account.Credentials.from_service_account_info(
        {
            "type": "service_account",
            "client_email": "test@lcftp.iam.gserviceaccount.com",
            "private_key": pem,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


@pytest.fixture
def store(gcs_endpoint: str) -> GcsObjectStore:
    client = storage.Client(
        project="test",
        credentials=AnonymousCredentials(),
        client_options={"api_endpoint": gcs_endpoint},
    )
    bucket = f"lcftp-{uuid4().hex[:12]}"
    client.create_bucket(bucket)
    return GcsObjectStore(
        bucket,
        client=client,
        signing_credentials=_signing_credentials(),
        api_access_endpoint=gcs_endpoint,
    )


async def test_signed_upload_and_stat(
    store: GcsObjectStore, tmp_path: Path
) -> None:
    path = tmp_path / "f.fits"
    path.write_bytes(b"hello\n")
    sums = compute_checksums(path)
    signed = await store.sign_upload("a/b.fits", 6, sums, timedelta(hours=1))
    assert signed.headers["x-goog-hash"] == (
        f"crc32c={sums.crc32c_b64},md5={sums.md5_b64}"
    )
    r = await _put(signed.url, b"hello\n", signed.headers)
    assert r.status_code == 200, r.text
    stat = await store.stat("a/b.fits")
    assert stat is not None
    assert stat.size_bytes == 6
    assert stat.crc32c_b64 == sums.crc32c_b64
    assert stat.md5_b64 == sums.md5_b64


async def test_missing_object(store: GcsObjectStore) -> None:
    assert await store.stat("nope") is None


@pytest.mark.xfail(
    strict=True, reason="fake-gcs-server does not validate x-goog-hash"
)
async def test_wrong_bytes_rejected(
    store: GcsObjectStore, tmp_path: Path
) -> None:
    path = tmp_path / "f.fits"
    path.write_bytes(b"hello\n")
    sums = compute_checksums(path)
    signed = await store.sign_upload("k", 6, sums, timedelta(hours=1))
    r = await _put(signed.url, b"HELLO\n", signed.headers)
    assert 400 <= r.status_code < 500


async def _put(
    url: str, data: bytes, headers: dict[str, str]
) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.put(url, content=data, headers=headers)
