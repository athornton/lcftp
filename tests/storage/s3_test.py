from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import boto3
import httpx
import pytest
from botocore.config import Config

from lcftp.storage.s3 import S3ObjectStore
from lcftp_models.checksums import compute_checksums
from tests.support.storage import S3Endpoint


@pytest.fixture
def store(s3_endpoint: S3Endpoint) -> S3ObjectStore:
    client = boto3.client(
        "s3",
        endpoint_url=s3_endpoint.url,
        region_name="us-west-2",
        aws_access_key_id=s3_endpoint.access_key,
        aws_secret_access_key=s3_endpoint.secret_key,
        config=Config(signature_version="s3v4"),
    )
    bucket = f"lcftp-{uuid4().hex[:12]}"
    client.create_bucket(
        Bucket=bucket,
        CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
    )
    return S3ObjectStore(bucket, client=client)


def _file(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "frame.fits"
    path.write_bytes(data)
    return path


async def test_signed_upload_and_stat(
    store: S3ObjectStore, tmp_path: Path
) -> None:
    sums = compute_checksums(_file(tmp_path, b"hello\n"))
    signed = await store.sign_upload(
        "lcftp/a/b.fits", 6, sums, timedelta(hours=1)
    )
    assert "x-amz-checksum-sha256" in signed.headers
    r = await _put(signed.url, b"hello\n", signed.headers)
    assert r.status_code == 200, r.text
    stat = await store.stat("lcftp/a/b.fits")
    assert stat is not None
    assert stat.size_bytes == 6
    assert stat.sha256 == sums.sha256


async def test_missing_object(store: S3ObjectStore) -> None:
    assert await store.stat("nope") is None


async def test_wrong_bytes_rejected(
    store: S3ObjectStore, s3_endpoint: S3Endpoint, tmp_path: Path
) -> None:
    if not s3_endpoint.validates_checksums:
        pytest.skip("S3 emulator does not enforce checksums (e.g. moto)")
    sums = compute_checksums(_file(tmp_path, b"hello\n"))
    signed = await store.sign_upload("k", 6, sums, timedelta(hours=1))
    r = await _put(signed.url, b"HELLO\n", signed.headers)
    assert 400 <= r.status_code < 500
    assert await store.stat("k") is None


async def _put(
    url: str, data: bytes, headers: dict[str, str]
) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.put(url, content=data, headers=headers)
