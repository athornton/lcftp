from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
from astropy.io import fits

from lcftp_client.provenance import commit_frame
from lcftp_client.spool import Spool
from lcftp_models.checksums import compute_checksums
from lcftp_models.plan import ObservationPlan


def test_commit_frame_stamps_and_commits(
    tmp_path: Path, plan: ObservationPlan
) -> None:
    spool = Spool(tmp_path)
    fid = uuid4()
    path = spool.new_frame_path(fid)
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.int16)).writeto(path)
    item = plan.items[1]
    record = commit_frame(
        spool,
        path,
        frame_id=fid,
        plan=plan,
        item=item,
        observatory_id="obs1",
        driver="ingest",
        driver_version="1.0",
        obs_start=datetime(2026, 11, 10, 3, 12, tzinfo=UTC),
        exposure_s=60.0,
        filter="r",
        ra=31.0,
        dec=10.0,
        driver_metadata={"source_path": "/x.fits"},
    )
    header = fits.getheader(path)
    assert header["LCFTPITM"] == "001"
    assert header["LCFTPDRV"] == "ingest/1.0"
    assert spool.pending_frames() == [record]
    assert record.checksums == compute_checksums(path)
    assert record.size_bytes == path.stat().st_size
    assert record.driver_metadata == {"source_path": "/x.fits"}
