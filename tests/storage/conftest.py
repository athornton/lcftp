"""Object store emulators for storage integration tests.

``LCFTP_TEST_S3_ENDPOINT`` and ``LCFTP_TEST_GCS_ENDPOINT`` point at
already-running emulators; otherwise MinIO and fake-gcs-server are started
with testcontainers. ``LCFTP_TEST_S3_VALIDATES=0`` marks an S3 emulator
(such as moto) that does not enforce checksums.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest


@dataclass
class S3Endpoint:
    url: str
    access_key: str
    secret_key: str
    validates_checksums: bool


@pytest.fixture(scope="session")
def s3_endpoint() -> Iterator[S3Endpoint]:
    if url := os.environ.get("LCFTP_TEST_S3_ENDPOINT"):
        validates = os.environ.get("LCFTP_TEST_S3_VALIDATES", "1") == "1"
        yield S3Endpoint(url, "testing", "testing", validates)
        return
    from testcontainers.minio import MinioContainer  # noqa: PLC0415

    with MinioContainer() as minio:
        config = minio.get_config()
        yield S3Endpoint(
            f"http://{config['endpoint']}",
            config["access_key"],
            config["secret_key"],
            validates_checksums=True,
        )


@pytest.fixture(scope="session")
def gcs_endpoint() -> Iterator[str]:
    if url := os.environ.get("LCFTP_TEST_GCS_ENDPOINT"):
        yield url
        return
    from testcontainers.core.container import DockerContainer  # noqa: PLC0415

    # Signed-URL uploads only work when the public host matches the URL the
    # client uses, so bind a fixed port.
    container = (
        DockerContainer("fsouza/fake-gcs-server:1.52.2")
        .with_command(
            "-scheme http -port 4443 -backend memory"
            " -public-host localhost:4443"
            " -external-url http://localhost:4443"
        )
        .with_bind_ports(4443, 4443)
    )
    with container:
        yield "http://localhost:4443"
