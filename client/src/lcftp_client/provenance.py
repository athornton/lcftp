"""Stamp a frame with provenance and commit it to the spool."""

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import JsonValue

from lcftp_models.checksums import compute_checksums
from lcftp_models.plan import ObservationPlan, PlanItem
from lcftp_models.upload import FrameRecord

from .fits import stamp_provenance
from .spool import Spool

__all__ = ["commit_frame"]


def commit_frame(
    spool: Spool,
    path: Path,
    *,
    frame_id: UUID,
    plan: ObservationPlan,
    item: PlanItem,
    observatory_id: str,
    driver: str,
    driver_version: str,
    obs_start: datetime,
    exposure_s: float,
    filter: str | None,  # noqa: A002
    ra: float,
    dec: float,
    driver_metadata: Mapping[str, JsonValue],
) -> FrameRecord:
    """Stamp ``path`` with provenance and make it eligible for upload.

    ``path`` must be ``spool.new_frame_path(frame_id)``. Checksums are
    computed after stamping, since stamping changes the file.
    """
    stamp_provenance(
        path,
        plan_id=plan.plan_id,
        item_id=item.item_id,
        observatory_id=observatory_id,
        dia_object_id=item.candidate.dia_object_id,
        alert_id=item.candidate.latest_alert_id,
        driver=driver,
        driver_version=driver_version,
    )
    record = FrameRecord(
        frame_id=frame_id,
        plan_id=plan.plan_id,
        item_id=item.item_id,
        observatory_id=observatory_id,
        dia_object_id=item.candidate.dia_object_id,
        alert_id=item.candidate.latest_alert_id,
        obs_start=obs_start,
        exposure_s=exposure_s,
        filter=filter,
        driver=driver,
        driver_version=driver_version,
        ra=ra,
        dec=dec,
        size_bytes=path.stat().st_size,
        checksums=compute_checksums(path),
        driver_metadata=dict(driver_metadata),
    )
    spool.commit_frame(record)
    return record
