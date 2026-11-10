"""Frame provenance, item outcomes and upload exchange models."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue

from ._base import ContractModel, UtcDatetime

__all__ = [
    "Checksums",
    "FrameRecord",
    "ItemOutcome",
    "OutcomeReason",
    "OutcomeStatus",
    "SignedUpload",
    "UploadManifest",
    "UploadResult",
    "UploadTicket",
]

MAX_FRAMES_PER_BATCH = 50

type OutcomeStatus = Literal["observed", "skipped", "failed"]


class OutcomeReason(StrEnum):
    """Why a plan item ended the way it did."""

    ok = "ok"
    window_passed = "window_passed"
    stopped = "stopped"
    driver_error = "driver_error"
    timeout = "timeout"
    weather = "weather"
    incomplete = "incomplete"
    not_visible = "not_visible"
    other = "other"


class ItemOutcome(ContractModel):
    """The result of attempting one plan item."""

    plan_id: str
    item_id: str
    status: OutcomeStatus
    reason: OutcomeReason
    detail: str | None = None
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None


class Checksums(ContractModel):
    """Checksums of a frame file."""

    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    """Hex SHA-256; the canonical provenance checksum."""

    md5_b64: str
    """Base64 MD5, as used by GCS and S3 ``Content-MD5``."""

    crc32c_b64: str
    """Base64 big-endian CRC32C, as used by GCS."""


class FrameRecord(ContractModel):
    """Provenance record for one captured frame."""

    frame_id: UUID
    plan_id: str
    item_id: str
    observatory_id: str
    dia_object_id: int
    alert_id: str | None
    obs_start: UtcDatetime
    exposure_s: Annotated[float, Field(gt=0)]
    filter: str | None
    driver: str
    driver_version: str
    ra: float
    """Actual pointing right ascension in degrees."""

    dec: float
    """Actual pointing declination in degrees."""

    size_bytes: Annotated[int, Field(gt=0)]
    checksums: Checksums
    driver_metadata: dict[str, JsonValue] = Field(default_factory=dict)


class UploadManifest(ContractModel):
    """A batch of frames and outcomes a client wants to upload."""

    batch_id: UUID
    frames: Annotated[
        list[FrameRecord], Field(max_length=MAX_FRAMES_PER_BATCH)
    ]
    outcomes: list[ItemOutcome] = Field(default_factory=list)


class SignedUpload(ContractModel):
    """Where and how to upload one frame."""

    frame_id: UUID
    url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]
    """Headers that must be sent verbatim; they bind the checksum."""

    expires_at: UtcDatetime


class UploadTicket(ContractModel):
    """Server response to an upload manifest."""

    batch_id: UUID
    uploads: list[SignedUpload]
    already_received: list[UUID]


class UploadResult(ContractModel):
    """Server verification of an upload batch."""

    batch_id: UUID
    received: list[UUID]
    missing: list[UUID]
    mismatched: list[UUID]
