"""The object store protocol."""

from datetime import timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from lcftp_models.upload import Checksums, SignedUpload

__all__ = ["ObjectStat", "ObjectStore"]


class ObjectStat(BaseModel):
    """Metadata of a stored object, as reported by the store."""

    model_config = ConfigDict(frozen=True)

    size_bytes: int
    sha256: str | None = None
    md5_b64: str | None = None
    crc32c_b64: str | None = None


class ObjectStore(Protocol):
    """Where frames are uploaded; the server never handles frame bytes."""

    async def sign_upload(
        self,
        key: str,
        size_bytes: int,
        checksums: Checksums,
        ttl: timedelta,
    ) -> SignedUpload:
        """Sign a PUT URL whose headers bind the content checksum.

        The returned ``frame_id`` is a placeholder; callers replace it.
        """

    async def stat(self, key: str) -> ObjectStat | None:
        """Return an object's metadata, or `None` if it does not exist."""
