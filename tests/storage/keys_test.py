from datetime import UTC, datetime
from uuid import UUID

from lcftp.storage.keys import frame_object_key
from lcftp_models.upload import Checksums, FrameRecord

FRAME_ID = UUID("12345678-1234-5678-1234-567812345678")


def _record() -> FrameRecord:
    return FrameRecord(
        frame_id=FRAME_ID,
        plan_id="plan1",
        item_id="000",
        observatory_id="obs1",
        dia_object_id=123,
        alert_id=None,
        obs_start=datetime(2026, 11, 10, 3, tzinfo=UTC),
        exposure_s=10,
        filter=None,
        driver="simulated",
        driver_version="1",
        ra=1,
        dec=2,
        size_bytes=6,
        checksums=Checksums(sha256="0" * 64, md5_b64="", crc32c_b64=""),
    )


def test_key_format() -> None:
    assert frame_object_key(_record()) == (
        f"alerts/123/obs1/plan1/{FRAME_ID}.fits"
    )


def test_key_prefix() -> None:
    assert frame_object_key(_record(), "lcftp/") == (
        f"lcftp/alerts/123/obs1/plan1/{FRAME_ID}.fits"
    )
