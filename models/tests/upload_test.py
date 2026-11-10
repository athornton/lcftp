from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from lcftp_models.errors import ErrorCode, ErrorResponse, LcftpError
from lcftp_models.upload import (
    Checksums,
    FrameRecord,
    ItemOutcome,
    OutcomeReason,
    UploadManifest,
)

CHECKSUMS = Checksums(
    sha256="5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03",
    md5_b64="sZRqySSS0jR8YjW00mERhA==",
    crc32c_b64="AAAAAA==",
)


def _frame(frame_id: UUID | None = None) -> FrameRecord:
    return FrameRecord(
        frame_id=frame_id or uuid4(),
        plan_id="p1",
        item_id="000",
        observatory_id="obs1",
        dia_object_id=123,
        alert_id=None,
        obs_start=datetime(2026, 11, 10, 3, 0, tzinfo=UTC),
        exposure_s=10.0,
        filter="r",
        driver="simulated",
        driver_version="1.0",
        ra=30.0,
        dec=-5.0,
        size_bytes=6,
        checksums=CHECKSUMS,
        driver_metadata={"gain": 1.5, "notes": ["a", None]},
    )


def test_checksums_require_hex_sha256() -> None:
    with pytest.raises(ValidationError):
        Checksums(sha256="xyz", md5_b64="a", crc32c_b64="b")


def test_frame_round_trip() -> None:
    frame = _frame()
    assert FrameRecord.model_validate_json(frame.model_dump_json()) == frame


def test_frame_requires_positive_size() -> None:
    data = _frame().model_dump()
    data["size_bytes"] = 0
    with pytest.raises(ValidationError):
        FrameRecord.model_validate(data)


def test_manifest_allows_50_frames() -> None:
    manifest = UploadManifest(
        batch_id=uuid4(), frames=[_frame() for _ in range(50)]
    )
    assert len(manifest.frames) == 50


def test_manifest_rejects_51_frames() -> None:
    with pytest.raises(ValidationError):
        UploadManifest(batch_id=uuid4(), frames=[_frame() for _ in range(51)])


def test_outcome_reasons() -> None:
    outcome = ItemOutcome(
        plan_id="p1",
        item_id="000",
        status="skipped",
        reason=OutcomeReason.window_passed,
        started_at=None,
        finished_at=None,
    )
    assert outcome.model_dump(mode="json")["reason"] == "window_passed"


def test_lcftp_error_carries_response() -> None:
    err = LcftpError(ErrorCode.not_found, "x", 404)
    assert err.status == 404
    assert err.to_response() == ErrorResponse(
        code=ErrorCode.not_found, message="x"
    )
    assert str(err) == "x"
