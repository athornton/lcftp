"""Shared fakes for the Seestar driver tests."""

from pathlib import Path

import numpy as np
from astropy.io import fits

from lcftp_driver_seestar.control import (
    DeviceInfo,
    GotoResult,
    SeestarControlError,
    StackInfo,
)

DEVICE = DeviceInfo(
    product_model="Seestar S30 Plus", firmware="9.75", focal_len_mm=160.0
)
NAME = "Light_t_10.0s_IRCUT_20261115-031000.fit"


def write_frame(path: Path, *, wcs: bool = True) -> None:
    header = fits.Header()
    header["EXPTIME"] = 10.0
    header["FILTER"] = "IRCUT"
    header["DATE-OBS"] = "2026-11-15T03:10:00.000"
    header["XPIXSZ"] = 2.9
    header["SN"] = "a3497936"
    if wcs:
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CRVAL1"] = 82.5
        header["CRVAL2"] = -5.4
        header["CRPIX1"] = 720.5
        header["CRPIX2"] = 1280.5
        header["CDELT1"] = -0.00117
        header["CDELT2"] = 0.00117
    data = np.zeros((2560, 1440), dtype=np.uint16)
    fits.PrimaryHDU(data, header).writeto(path)


class FakeControl:
    """Scripted SeestarControl for recording tests."""

    def __init__(self) -> None:
        self.stacked = 0

    def connect(self, host: str | None, key_path: Path | None) -> DeviceInfo:
        return DEVICE

    def open_arm(self) -> None:
        pass

    def goto(
        self,
        target_name: str,
        ra_hours: float,
        dec_deg: float,
        timeout_s: float,
    ) -> GotoResult:
        return GotoResult(
            state="complete",
            ra_hours=ra_hours,
            dec_deg=dec_deg,
            dist_deg=0.01,
            error=None,
        )

    def set_exposure(self, seconds: int) -> None:
        pass

    def set_save_subframes(self, *, enabled: bool) -> None:
        pass

    def start_stack(self) -> None:
        pass

    def stack_info(self) -> StackInfo:
        self.stacked += 1
        return StackInfo(
            stacked_frames=self.stacked, dropped_frames=0, running=True
        )

    def stop_stack(self) -> None:
        raise SeestarControlError("already stopped")

    def list_subframes(self, target_name: str) -> list[str]:
        return [NAME]

    def download(self, target_name: str, name: str, dest: Path) -> Path:
        write_frame(dest)
        return dest

    def park(self) -> None:
        pass

    def disconnect(self) -> None:
        pass
