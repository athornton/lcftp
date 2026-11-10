"""Fixtures running the whole system: server, database, bucket, client."""

import asyncio
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import boto3
import pytest
import pytest_asyncio
import uvicorn
from botocore.config import Config

from lcftp.config import Settings
from lcftp.main import create_app
from tests.support.storage import S3Endpoint, s3_endpoint

__all__ = ["Stack", "s3_endpoint", "stack"]


@dataclass
class Stack:
    """A running LCFTP server and its bucket."""

    url: str
    settings: Settings
    bucket: str
    s3: object


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def stack_settings(
    settings: Settings,
    s3_endpoint: S3Endpoint,
    monkeypatch: pytest.MonkeyPatch,
) -> Settings:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", s3_endpoint.access_key)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", s3_endpoint.secret_key)
    bucket = f"lcftp-e2e-{uuid4().hex[:8]}"
    s3 = boto3.client(
        "s3",
        endpoint_url=s3_endpoint.url,
        region_name="us-west-2",
        config=Config(signature_version="s3v4"),
    )
    s3.create_bucket(
        Bucket=bucket,
        CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
    )
    port = _free_port()
    return settings.model_copy(
        update={
            "storage_backend": "s3",
            "storage_bucket": bucket,
            "storage_prefix": "lcftp/",
            "storage_region": "us-west-2",
            "storage_endpoint_url": s3_endpoint.url,
            "base_url": f"http://127.0.0.1:{port}",
        }
    )


@pytest_asyncio.fixture
async def stack(stack_settings: Settings) -> AsyncIterator[Stack]:
    url = str(stack_settings.base_url).rstrip("/")
    port = int(url.rsplit(":", 1)[1])
    config = uvicorn.Config(
        create_app(stack_settings),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    s3 = boto3.client(
        "s3",
        endpoint_url=str(stack_settings.storage_endpoint_url),
        region_name="us-west-2",
        config=Config(signature_version="s3v4"),
    )
    try:
        yield Stack(url, stack_settings, stack_settings.storage_bucket, s3)
    finally:
        server.should_exit = True
        await task
