"""In-memory object store for tests and local development."""

import base64
import hashlib
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID

import google_crc32c

from lcftp_models.upload import Checksums, SignedUpload

from .base import ObjectStat

__all__ = ["SHA256_HEADER", "MemoryObjectStore"]

SHA256_HEADER = "x-lcftp-sha256"

type _Scope = MutableMapping[str, Any]
type _Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
type _Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


class MemoryObjectStore:
    """Object store holding objects in a dict, with an ASGI PUT endpoint.

    Uploads go through `asgi_app` (for example via
    ``httpx.ASGITransport``), which enforces the signed checksum header
    and URL expiry like a real bucket.
    """

    def __init__(self, base_url: str = "http://memory.invalid") -> None:
        self.base_url = base_url.rstrip("/")
        self.objects: dict[str, bytes] = {}

    async def sign_upload(
        self,
        key: str,
        size_bytes: int,
        checksums: Checksums,
        ttl: timedelta,
    ) -> SignedUpload:
        """Return a URL with an expiry and a checksum-binding header."""
        expires = datetime.now(tz=UTC) + ttl
        return SignedUpload(
            frame_id=UUID(int=0),
            url=f"{self.base_url}/{key}?expires={int(expires.timestamp())}",
            headers={SHA256_HEADER: checksums.sha256},
            expires_at=expires,
        )

    async def stat(self, key: str) -> ObjectStat | None:
        """Return checksums computed from the stored bytes."""
        data = self.objects.get(key)
        if data is None:
            return None
        crc = google_crc32c.value(data).to_bytes(4, "big")
        md5 = hashlib.md5(data, usedforsecurity=False).digest()
        return ObjectStat(
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            md5_b64=base64.b64encode(md5).decode(),
            crc32c_b64=base64.b64encode(crc).decode(),
        )

    def put(self, key: str, data: bytes) -> None:
        """Store an object directly (test hook)."""
        self.objects[key] = data

    def sha256_of(self, key: str) -> str:
        """Return the SHA-256 of a stored object."""
        return hashlib.sha256(self.objects[key]).hexdigest()

    async def asgi_app(
        self, scope: _Scope, receive: _Receive, send: _Send
    ) -> None:
        """Accept ``PUT /<key>`` uploads signed by `sign_upload`."""
        if scope["type"] != "http" or scope["method"] != "PUT":
            await _respond(send, 405)
            return
        query = parse_qs(scope.get("query_string", b"").decode())
        expires = int(query.get("expires", ["0"])[0])
        if datetime.now(tz=UTC).timestamp() > expires:
            await _respond(send, 403, b"Request has expired")
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if not message.get("more_body"):
                break
        if hashlib.sha256(body).hexdigest() != headers.get(SHA256_HEADER):
            await _respond(send, 400, b"BadDigest")
            return
        self.objects[scope["path"].lstrip("/")] = bytes(body)
        await _respond(send, 200)


async def _respond(send: _Send, status: int, body: bytes = b"") -> None:
    await send(
        {"type": "http.response.start", "status": status, "headers": []}
    )
    await send({"type": "http.response.body", "body": body})
