"""Object key layout."""

from lcftp_models.upload import FrameRecord

__all__ = ["frame_object_key"]


def frame_object_key(record: FrameRecord, prefix: str = "") -> str:
    """Return the object key for a frame, grouped by alert and observatory."""
    return (
        f"{prefix}alerts/{record.dia_object_id}/{record.observatory_id}"
        f"/{record.plan_id}/{record.frame_id}.fits"
    )
