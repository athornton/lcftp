"""Google Cloud Storage object store."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from google.cloud import storage

from lcftp_models.upload import Checksums, SignedUpload

from .base import ObjectStat

__all__ = ["GcsObjectStore"]


class GcsObjectStore:
    """Object store backed by a GCS bucket.

    Upload URLs are V4-signed with ``x-goog-hash`` (CRC32C and MD5) and
    ``x-goog-meta-sha256`` as required headers, so GCS rejects any upload
    whose body does not match.
    """

    def __init__(
        self,
        bucket: str,
        *,
        client: storage.Client | None = None,
        signing_credentials: Any = None,
        api_access_endpoint: str | None = None,
    ) -> None:
        self._client = client or storage.Client()
        self._bucket = self._client.bucket(bucket)
        self._signing_credentials = signing_credentials
        self._endpoint = api_access_endpoint

    async def sign_upload(
        self,
        key: str,
        size_bytes: int,
        checksums: Checksums,
        ttl: timedelta,
    ) -> SignedUpload:
        """Sign a V4 PUT URL bound to the frame's CRC32C and MD5."""
        headers = {
            "x-goog-hash": (
                f"crc32c={checksums.crc32c_b64},md5={checksums.md5_b64}"
            ),
            "x-goog-meta-sha256": checksums.sha256,
        }
        kwargs: dict[str, Any] = {
            "version": "v4",
            "method": "PUT",
            "expiration": ttl,
            "headers": headers,
        }
        if self._signing_credentials is not None:
            kwargs["credentials"] = self._signing_credentials
        if self._endpoint:
            kwargs["api_access_endpoint"] = self._endpoint
        blob = self._bucket.blob(key)
        url = await asyncio.to_thread(blob.generate_signed_url, **kwargs)
        return SignedUpload(
            frame_id=UUID(int=0),
            url=url,
            headers=headers,
            expires_at=datetime.now(tz=UTC) + ttl,
        )

    async def stat(self, key: str) -> ObjectStat | None:
        """Return size and checksums from object metadata."""
        blob = await asyncio.to_thread(self._bucket.get_blob, key)
        if blob is None:
            return None
        metadata = blob.metadata or {}
        return ObjectStat(
            size_bytes=int(blob.size or 0),
            sha256=metadata.get("sha256"),
            md5_b64=blob.md5_hash,
            crc32c_b64=blob.crc32c,
        )
