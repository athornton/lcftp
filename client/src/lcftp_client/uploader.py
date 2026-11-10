"""Resumable, checksum-verified upload of spooled frames."""

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from typing import Protocol
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel

from lcftp_models.upload import (
    FrameRecord,
    ItemOutcome,
    SignedUpload,
    UploadManifest,
    UploadResult,
    UploadTicket,
)

from .api import LcftpApiError, LcftpAuthError
from .spool import Spool

__all__ = ["UploadServer", "UploadSummary", "Uploader"]


class UploadServer(Protocol):
    """The server calls the uploader needs (see `~lcftp_client.api`)."""

    async def create_upload(self, manifest: UploadManifest) -> UploadTicket:
        """Send a manifest; receive signed URLs."""

    async def complete_upload(self, batch_id: UUID) -> UploadResult:
        """Ask the server to verify a batch."""


class UploadSummary(BaseModel):
    """What one flush accomplished."""

    uploaded: int = 0
    already_received: int = 0
    bad: int = 0
    remaining: int = 0


class _GiveUpError(Exception):
    """Retries were exhausted; leave the rest for the next flush."""


class Uploader:
    """Upload pending frames and outcomes from the spool.

    Frames are deleted only after the server confirms receipt. Network
    failures are retried with jittered exponential backoff; an upload the
    bucket rejects is re-signed and retried once, then set aside as bad.
    Authentication errors stop the upload without deleting anything.
    """

    def __init__(
        self,
        server: UploadServer,
        spool: Spool,
        *,
        http: httpx.AsyncClient | None = None,
        batch_size: int = 50,
        max_attempts: int = 5,
        base_delay_s: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._server = server
        self._spool = spool
        self._http = http or httpx.AsyncClient(timeout=300)
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._base_delay = base_delay_s
        self._sleep = sleep

    async def flush(self) -> UploadSummary:
        """Upload everything pending; return what happened."""
        summary = UploadSummary()
        frames = self._spool.pending_frames()
        outcomes = self._spool.unreported_outcomes()
        batches = [
            frames[i : i + self._batch_size]
            for i in range(0, len(frames), self._batch_size)
        ]
        if not batches and outcomes:
            batches = [[]]
        for n, batch in enumerate(batches):
            try:
                await self._upload_batch(
                    batch, outcomes if n == 0 else [], summary
                )
            except _GiveUpError:
                break
        summary.remaining = len(self._spool.pending_frames())
        return summary

    async def _upload_batch(
        self,
        frames: Sequence[FrameRecord],
        outcomes: Sequence[ItemOutcome],
        summary: UploadSummary,
    ) -> None:
        manifest = UploadManifest(
            batch_id=uuid4(), frames=list(frames), outcomes=list(outcomes)
        )
        ticket = await self._retry(
            lambda: self._server.create_upload(manifest)
        )
        self._spool.mark_outcomes_reported(outcomes)
        for frame_id in ticket.already_received:
            self._spool.remove_frame(frame_id)
            summary.already_received += 1

        sent = {u.frame_id for u in ticket.uploads}
        rejected = await self._put_all(ticket.uploads)
        if rejected:
            await self._resend(manifest, rejected, summary)

        result = await self._retry(
            lambda: self._server.complete_upload(manifest.batch_id)
        )
        if result.mismatched:
            await self._resend(manifest, set(result.mismatched), summary)
            result = await self._retry(
                lambda: self._server.complete_upload(manifest.batch_id)
            )
            for frame_id in result.mismatched:
                self._spool.mark_bad(frame_id, "checksum mismatch after retry")
                summary.bad += 1
        for frame_id in result.received:
            if frame_id in sent:
                summary.uploaded += 1
            self._spool.remove_frame(frame_id)

    async def _resend(
        self,
        manifest: UploadManifest,
        frame_ids: set[UUID],
        summary: UploadSummary,
    ) -> None:
        """Re-sign and re-upload frames once; set aside any still rejected."""
        ticket = await self._retry(
            lambda: self._server.create_upload(manifest)
        )
        retry = [u for u in ticket.uploads if u.frame_id in frame_ids]
        for frame_id in await self._put_all(retry):
            self._spool.mark_bad(frame_id, "upload rejected by bucket twice")
            summary.bad += 1

    async def _put_all(self, uploads: Sequence[SignedUpload]) -> set[UUID]:
        """PUT each frame; return ids the bucket rejected with a 4xx."""
        rejected = set()
        for upload in uploads:
            path = self._spool.frame_path(upload.frame_id)
            if not path.exists():
                continue
            data = await asyncio.to_thread(path.read_bytes)
            response = await self._retry(partial(self._put, upload, data))
            if response.is_client_error:
                rejected.add(upload.frame_id)
        return rejected

    async def _put(self, upload: SignedUpload, data: bytes) -> httpx.Response:
        response = await self._http.put(
            upload.url, content=data, headers=upload.headers
        )
        if response.is_server_error:
            response.raise_for_status()
        return response

    async def _retry[T](self, call: Callable[[], Awaitable[T]]) -> T:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await call()
            except LcftpAuthError:
                raise
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                error: Exception = e
            except LcftpApiError as e:
                if e.status < 500:
                    raise
                error = e
            if attempt == self._max_attempts:
                raise _GiveUpError from error
            limit = self._base_delay * 2**attempt
            await self._sleep(random.uniform(0, limit))  # noqa: S311
        raise AssertionError("unreachable")  # pragma: no cover
