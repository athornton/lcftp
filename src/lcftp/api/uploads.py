"""Upload manifests and completion for observatory clients."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from lcftp_models.errors import ErrorCode, LcftpError
from lcftp_models.plan import ObservationPlan
from lcftp_models.upload import (
    FrameRecord,
    UploadManifest,
    UploadResult,
    UploadTicket,
)

from ..db.schema import FrameRow
from ..storage.base import ObjectStat, ObjectStore
from ..storage.keys import frame_object_key
from ..stores.plans import SqlPlanStore
from ..stores.uploads import SqlUploadStore
from .deps import DeviceDep, SessionDep, SettingsDep

__all__ = ["get_store", "router"]

router = APIRouter(prefix="/api", tags=["uploads"])


def get_store(request: Request) -> ObjectStore:
    """Return the configured object store."""
    store: ObjectStore = request.app.state.store
    return store


type StoreDep = Annotated[ObjectStore, Depends(get_store)]


async def _check_item(
    plans: dict[str, ObservationPlan | None],
    store: SqlPlanStore,
    observatory_id: str,
    plan_id: str,
    item_id: str,
) -> None:
    if plan_id not in plans:
        status = await store.get(plan_id)
        plans[plan_id] = status.plan if status else None
    plan = plans[plan_id]
    if (
        plan is None
        or plan.observatory_id != observatory_id
        or item_id not in {i.item_id for i in plan.items}
    ):
        msg = f"Item {item_id} is not part of plan {plan_id}"
        raise LcftpError(ErrorCode.frame_not_in_plan, msg, 422)


def _validate_frame(
    record: FrameRecord, observatory_id: str, max_bytes: int
) -> None:
    if record.observatory_id != observatory_id:
        msg = f"Frame {record.frame_id} belongs to another observatory"
        raise LcftpError(ErrorCode.forbidden, msg, 403)
    if record.size_bytes > max_bytes:
        msg = f"Frame {record.frame_id} exceeds {max_bytes} bytes"
        raise LcftpError(ErrorCode.frame_too_large, msg, 422)


@router.post("/uploads")
async def post_upload(
    manifest: UploadManifest,
    device: DeviceDep,
    session: SessionDep,
    settings: SettingsDep,
    store: StoreDep,
) -> UploadTicket:
    """Validate a manifest and return signed upload URLs."""
    observatory_id = device.observatory_id
    uploads = SqlUploadStore(session)
    owner = await uploads.batch_owner(manifest.batch_id)
    if owner is not None and owner != observatory_id:
        msg = f"Batch {manifest.batch_id} belongs to another observatory"
        raise LcftpError(ErrorCode.forbidden, msg, 403)

    plan_store = SqlPlanStore(session)
    plans: dict[str, ObservationPlan | None] = {}
    for record in manifest.frames:
        _validate_frame(record, observatory_id, settings.max_frame_bytes)
        await _check_item(
            plans, plan_store, observatory_id, record.plan_id, record.item_id
        )
    for outcome in manifest.outcomes:
        await _check_item(
            plans, plan_store, observatory_id, outcome.plan_id, outcome.item_id
        )

    now = datetime.now(tz=UTC)
    await uploads.ensure_batch(manifest.batch_id, observatory_id, now)
    existing = await uploads.frames([f.frame_id for f in manifest.frames])
    already = []
    signed = []
    for record in manifest.frames:
        row = existing.get(record.frame_id)
        if row is not None and row.status == "received":
            already.append(record.frame_id)
            continue
        key = frame_object_key(record, settings.storage_prefix)
        await uploads.upsert_pending(record, manifest.batch_id, key)
        upload = await store.sign_upload(
            key, record.size_bytes, record.checksums, settings.upload_url_ttl
        )
        signed.append(upload.model_copy(update={"frame_id": record.frame_id}))
    for outcome in manifest.outcomes:
        await uploads.add_outcome(outcome, observatory_id, now)
    return UploadTicket(
        batch_id=manifest.batch_id, uploads=signed, already_received=already
    )


def _matches(row: FrameRow, stat: ObjectStat) -> bool:
    if stat.size_bytes != row.size_bytes:
        return False
    record = FrameRecord.model_validate(row.record)
    expected = {
        "sha256": record.checksums.sha256,
        "crc32c_b64": record.checksums.crc32c_b64,
        "md5_b64": record.checksums.md5_b64,
    }
    return all(
        getattr(stat, name) in (None, value)
        for name, value in expected.items()
    )


@router.post("/uploads/{batch_id}/complete")
async def complete_upload(
    batch_id: UUID,
    device: DeviceDep,
    session: SessionDep,
    store: StoreDep,
) -> UploadResult:
    """Verify uploaded objects by metadata and record them as received."""
    uploads = SqlUploadStore(session)
    if await uploads.batch_owner(batch_id) != device.observatory_id:
        msg = f"Batch {batch_id} not found"
        raise LcftpError(ErrorCode.not_found, msg, 404)
    now = datetime.now(tz=UTC)
    result = UploadResult(
        batch_id=batch_id, received=[], missing=[], mismatched=[]
    )
    for row in await uploads.frames_in_batch(batch_id):
        if row.status == "received":
            result.received.append(row.frame_id)
            continue
        stat = await store.stat(row.object_key)
        if stat is None:
            result.missing.append(row.frame_id)
        elif _matches(row, stat):
            await uploads.set_status(row.frame_id, "received", now)
            result.received.append(row.frame_id)
        else:
            await uploads.set_status(row.frame_id, "mismatched", None)
            result.mismatched.append(row.frame_id)
    await uploads.complete_batch(batch_id, now)
    return result
