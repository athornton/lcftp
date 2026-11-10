"""Object store emulators for storage and end-to-end tests.

``LCFTP_TEST_S3_ENDPOINT`` and ``LCFTP_TEST_GCS_ENDPOINT`` point at
already-running emulators; otherwise they are started
in-process (S3, via moto) or with testcontainers (GCS).
``LCFTP_TEST_S3_VALIDATES=0`` marks an S3 emulator (such as moto) that does
not enforce checksums.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

__all__ = ["S3Endpoint", "gcs_endpoint", "s3_endpoint"]


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
    # MinIO images are no longer publicly pullable, so run moto in-process.
    # moto does not enforce checksums; tests that need that are skipped.
    from moto.server import ThreadedMotoServer  # noqa: PLC0415

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    try:
        yield S3Endpoint(
            f"http://{host}:{port}",
            "testing",
            "testing",
            validates_checksums=False,
        )
    finally:
        server.stop()


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
