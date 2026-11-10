"""Compute the checksums recorded for each frame."""

import base64
import hashlib
from pathlib import Path

import google_crc32c

from .upload import Checksums

__all__ = ["compute_checksums"]


def compute_checksums(path: Path, chunk_size: int = 1 << 20) -> Checksums:
    """Compute SHA-256, MD5 and CRC32C of a file in one streaming pass."""
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    crc = google_crc32c.Checksum()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            sha256.update(chunk)
            md5.update(chunk)
            crc.update(chunk)
    return Checksums(
        sha256=sha256.hexdigest(),
        md5_b64=base64.b64encode(md5.digest()).decode(),
        crc32c_b64=base64.b64encode(crc.digest()).decode(),
    )
