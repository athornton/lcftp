import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, override
from uuid import UUID, uuid4

import httpx
import pytest

from lcftp_client.api import LcftpApiError, LcftpAuthError
from lcftp_client.spool import Spool
from lcftp_client.uploader import Uploader
from lcftp_models.checksums import compute_checksums
from lcftp_models.errors import ErrorCode
from lcftp_models.upload import (
    FrameRecord,
    ItemOutcome,
    OutcomeReason,
    SignedUpload,
    UploadManifest,
    UploadResult,
    UploadTicket,
)

T0 = datetime(2026, 11, 10, 3, tzinfo=UTC)


class FakeServer:
    """Server-side upload logic over a FakeBucket."""

    def __init__(self, bucket: Any) -> None:
        self.bucket = bucket
        self.frames: dict[UUID, FrameRecord] = {}
        self.batches: dict[UUID, list[UUID]] = {}
        self.received: set[UUID] = set()
        self.outcomes: list[ItemOutcome] = []
        self.creates = 0
        self.transport_errors = 0
        self.lose_complete_responses = 0
        self.auth_error = False

    async def create_upload(self, manifest: UploadManifest) -> UploadTicket:
        if self.auth_error:
            raise LcftpAuthError(ErrorCode.not_authenticated, "revoked", 401)
        if self.transport_errors > 0:
            self.transport_errors -= 1
            raise httpx.ConnectError("network down")
        self.creates += 1
        self.outcomes.extend(manifest.outcomes)
        self.batches.setdefault(manifest.batch_id, [])
        uploads, already = [], []
        for record in manifest.frames:
            self.frames[record.frame_id] = record
            if record.frame_id not in self.batches[manifest.batch_id]:
                self.batches[manifest.batch_id].append(record.frame_id)
            if record.frame_id in self.received:
                already.append(record.frame_id)
                continue
            uploads.append(
                SignedUpload(
                    frame_id=record.frame_id,
                    url=f"http://bucket.invalid/{record.frame_id}",
                    headers={"x-lcftp-sha256": record.checksums.sha256},
                    expires_at=T0 + timedelta(hours=1),
                )
            )
        return UploadTicket(
            batch_id=manifest.batch_id,
            uploads=uploads,
            already_received=already,
        )

    async def complete_upload(self, batch_id: UUID) -> UploadResult:
        result = UploadResult(
            batch_id=batch_id, received=[], missing=[], mismatched=[]
        )
        for frame_id in self.batches[batch_id]:
            data = self.bucket.stored.get(str(frame_id))
            expected = self.frames[frame_id].checksums.sha256
            if frame_id in self.received:
                result.received.append(frame_id)
            elif data is None:
                result.missing.append(frame_id)
            elif hashlib.sha256(data).hexdigest() == expected:
                self.received.add(frame_id)
                result.received.append(frame_id)
            else:
                result.mismatched.append(frame_id)
        if self.lose_complete_responses > 0:
            self.lose_complete_responses -= 1
            raise httpx.ReadTimeout("response lost")
        return result


def _add_frame(spool: Spool, data: bytes = b"SIMPLE  = T") -> FrameRecord:
    frame_id = uuid4()
    path = spool.new_frame_path(frame_id)
    path.write_bytes(data)
    record = FrameRecord(
        frame_id=frame_id,
        plan_id="plan1",
        item_id="000",
        observatory_id="obs1",
        dia_object_id=100,
        alert_id=None,
        obs_start=T0,
        exposure_s=10,
        filter="r",
        driver="simulated",
        driver_version="1",
        ra=30,
        dec=10,
        size_bytes=len(data),
        checksums=compute_checksums(path),
    )
    spool.commit_frame(record)
    return record


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)


@pytest.fixture
def spool(tmp_path: Path) -> Spool:
    return Spool(tmp_path / "spool")


def _uploader(
    server: FakeServer, spool: Spool, bucket: Any, sleeps: Sleeps | None = None
) -> Uploader:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=bucket))
    return Uploader(server, spool, http=http, sleep=sleeps or Sleeps())


async def test_happy_path(spool: Spool, bucket: Any) -> None:
    server = FakeServer(bucket)
    records = [_add_frame(spool, f"frame {i}".encode()) for i in range(3)]
    outcome = ItemOutcome(
        plan_id="plan1",
        item_id="000",
        status="observed",
        reason=OutcomeReason.ok,
        started_at=T0,
        finished_at=T0,
    )
    spool.record_outcome(outcome)
    summary = await _uploader(server, spool, bucket).flush()
    assert summary.uploaded == 3
    assert summary.remaining == 0
    assert spool.pending_frames() == []
    assert list(spool.frames_dir.iterdir()) == []
    assert server.received == {r.frame_id for r in records}
    assert server.outcomes == [outcome]
    assert spool.unreported_outcomes() == []


async def test_batches(spool: Spool, bucket: Any) -> None:
    server = FakeServer(bucket)
    for i in range(5):
        _add_frame(spool, f"frame {i}".encode())
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=bucket))
    uploader = Uploader(server, spool, http=http, batch_size=2, sleep=Sleeps())
    summary = await uploader.flush()
    assert summary.uploaded == 5
    assert len(server.batches) == 3


async def test_rejected_put_is_resigned(spool: Spool, bucket: Any) -> None:
    server = FakeServer(bucket)
    record = _add_frame(spool)
    bucket.reject_next = 1
    summary = await _uploader(server, spool, bucket).flush()
    assert summary.uploaded == 1
    assert server.creates == 2
    assert record.frame_id in server.received


async def test_corrupted_frame_goes_to_bad(spool: Spool, bucket: Any) -> None:
    server = FakeServer(bucket)
    record = _add_frame(spool)
    spool.frame_path(record.frame_id).write_bytes(b"bit rot")
    summary = await _uploader(server, spool, bucket).flush()
    assert summary.bad == 1
    assert summary.uploaded == 0
    assert (spool.bad_dir / f"{record.frame_id}.fits").exists()
    assert spool.pending_frames() == []


async def test_auth_error_keeps_spool(spool: Spool, bucket: Any) -> None:
    server = FakeServer(bucket)
    server.auth_error = True
    record = _add_frame(spool)
    with pytest.raises(LcftpAuthError):
        await _uploader(server, spool, bucket).flush()
    assert spool.pending_frames() == [record]


async def test_transport_errors_back_off(spool: Spool, bucket: Any) -> None:
    server = FakeServer(bucket)
    server.transport_errors = 3
    _add_frame(spool)
    sleeps = Sleeps()
    summary = await _uploader(server, spool, bucket, sleeps).flush()
    assert summary.uploaded == 1
    assert len(sleeps.calls) == 3
    assert all(0 <= d <= 2 ** (i + 1) for i, d in enumerate(sleeps.calls))


async def test_gives_up_after_max_attempts(spool: Spool, bucket: Any) -> None:
    server = FakeServer(bucket)
    server.transport_errors = 99
    record = _add_frame(spool)
    summary = await _uploader(server, spool, bucket).flush()
    assert summary.remaining == 1
    assert spool.pending_frames() == [record]


async def test_lost_complete_response(spool: Spool, bucket: Any) -> None:
    server = FakeServer(bucket)
    server.lose_complete_responses = 5
    record = _add_frame(spool)
    first = await _uploader(server, spool, bucket).flush()
    assert first.remaining == 1
    assert record.frame_id in server.received
    puts = bucket.puts

    second = await _uploader(server, spool, bucket).flush()
    assert second.already_received == 1
    assert spool.pending_frames() == []
    assert bucket.puts == puts


async def test_nothing_to_do(spool: Spool, bucket: Any) -> None:
    summary = await _uploader(FakeServer(bucket), spool, bucket).flush()
    assert summary.uploaded == summary.remaining == summary.bad == 0


async def test_bucket_403_keeps_frames(spool: Spool, bucket: Any) -> None:
    server = FakeServer(bucket)
    records = [_add_frame(spool, f"frame {i}".encode()) for i in range(3)]
    bucket.reject_next = 99
    summary = await _uploader(server, spool, bucket).flush()
    assert summary.bad == 0
    assert summary.remaining == 3
    assert spool.pending_frames() == sorted(
        records, key=lambda r: (r.obs_start, str(r.frame_id))
    )


class PickyServer(FakeServer):
    def __init__(self, bucket: Any, poison: UUID) -> None:
        super().__init__(bucket)
        self.poison = poison

    @override
    async def create_upload(self, manifest: UploadManifest) -> UploadTicket:
        if any(f.frame_id == self.poison for f in manifest.frames):
            raise LcftpApiError(
                ErrorCode.frame_not_in_plan, "no such item", 422
            )
        return await super().create_upload(manifest)


async def test_rejected_manifest_isolates_bad_frame(
    spool: Spool, bucket: Any
) -> None:
    records = [_add_frame(spool, f"frame {i}".encode()) for i in range(3)]
    server = PickyServer(bucket, records[1].frame_id)
    summary = await _uploader(server, spool, bucket).flush()
    assert summary.uploaded == 2
    assert summary.bad == 1
    assert summary.remaining == 0
    reason = (spool.bad_dir / f"{records[1].frame_id}.reason").read_text()
    assert "no such item" in reason
