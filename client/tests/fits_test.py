from pathlib import Path

import numpy as np
from astropy.io import fits

from lcftp_client.fits import stamp_provenance


def test_stamp_provenance(tmp_path: Path) -> None:
    path = tmp_path / "f.fits"
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.int16)).writeto(path)
    stamp_provenance(
        path,
        plan_id="plan1",
        item_id="002",
        observatory_id="obs1",
        dia_object_id=1234567890123,
        alert_id=None,
        driver="simulated",
        driver_version="1.2",
    )
    header = fits.getheader(path)
    assert header["LCFTPPLN"] == "plan1"
    assert header["LCFTPITM"] == "002"
    assert header["LCFTPOBS"] == "obs1"
    assert header["LCFTPDIA"] == 1234567890123
    assert header["LCFTPALR"] == ""
    assert header["LCFTPDRV"] == "simulated/1.2"
