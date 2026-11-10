"""Amazon S3 (and S3-compatible) object store."""

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from lcftp_models.upload import Checksums, SignedUpload

from .base import ObjectStat

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

__all__ = ["S3ObjectStore"]


class S3ObjectStore:
    """Object store backed by an S3 bucket.

    Upload URLs are presigned with ``x-amz-checksum-sha256`` and
    ``x-amz-meta-sha256`` among the signed headers, so S3 rejects any
    upload whose body does not match the recorded SHA-256.
    """

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str | None = None,
        client: S3Client | None = None,
    ) -> None:
        self._bucket = bucket
        self._client: S3Client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            config=Config(signature_version="s3v4"),
        )

    async def sign_upload(
        self,
        key: str,
        size_bytes: int,
        checksums: Checksums,
        ttl: timedelta,
    ) -> SignedUpload:
        """Presign a PUT bound to the frame's SHA-256."""
        sha256_b64 = base64.b64encode(bytes.fromhex(checksums.sha256)).decode()
        url = await asyncio.to_thread(
            self._client.generate_presigned_url,
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ChecksumSHA256": sha256_b64,
                "Metadata": {"sha256": checksums.sha256},
            },
            ExpiresIn=int(ttl.total_seconds()),
        )
        return SignedUpload(
            frame_id=UUID(int=0),
            url=url,
            headers={
                "x-amz-checksum-sha256": sha256_b64,
                "x-amz-meta-sha256": checksums.sha256,
            },
            expires_at=datetime.now(tz=UTC) + ttl,
        )

    async def stat(self, key: str) -> ObjectStat | None:
        """Return size and checksums from object metadata."""
        try:
            head: Any = await asyncio.to_thread(
                self._client.head_object,
                Bucket=self._bucket,
                Key=key,
                ChecksumMode="ENABLED",
            )
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                return None
            raise
        checksum = head.get("ChecksumSHA256")
        sha256 = (
            base64.b64decode(checksum).hex()
            if checksum
            else head.get("Metadata", {}).get("sha256")
        )
        return ObjectStat(size_bytes=int(head["ContentLength"]), sha256=sha256)
