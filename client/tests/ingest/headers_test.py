from datetime import UTC, datetime
from pathlib import Path

import pytest
from support_fits import write_fits

from lcftp_client.ingest.headers import HeaderError, read_frame_info


def test_basic(tmp_path: Path) -> None:
    path = write_fits(
        tmp_path / "a.fits",
        DATE_OBS="2026-11-24T03:15:00.123",
        EXPTIME=60.0,
        FILTER="r",
        RA=31.0,
        DEC=10.0,
        OBJECT="lcftp-plan1-001",
    )
    info = read_frame_info(path)
    assert info.obs_start == datetime(2026, 11, 24, 3, 15, 0, 123000, UTC)
    assert info.exposure_s == 60.0
    assert info.filter == "r"
    assert info.ra == 31.0
    assert info.dec == 10.0
    assert info.object == "lcftp-plan1-001"
    assert not info.lcftp_stamped
    assert info.metadata == {}


def test_exposure_fallback(tmp_path: Path) -> None:
    path = write_fits(
        tmp_path / "a.fits", DATE_OBS="2026-11-24T03:15:00", EXPOSURE=30
    )
    assert read_frame_info(path).exposure_s == 30


def test_objctra_objctdec(tmp_path: Path) -> None:
    path = write_fits(
        tmp_path / "a.fits",
        DATE_OBS="2026-11-24T03:15:00",
        EXPTIME=10,
        OBJCTRA="02 04 00.0",
        OBJCTDEC="+10 00 00",
    )
    info = read_frame_info(path)
    assert info.ra == pytest.approx(31.0)
    assert info.dec == pytest.approx(10.0)


def test_missing_pointing(tmp_path: Path) -> None:
    path = write_fits(
        tmp_path / "a.fits", DATE_OBS="2026-11-24T03:15:00", EXPTIME=10
    )
    info = read_frame_info(path)
    assert info.ra is None
    assert info.dec is None
    assert info.filter is None


@pytest.mark.parametrize(
    "cards",
    [
        {"EXPTIME": 10},
        {"DATE_OBS": "2026-11-24T03:15:00", "EXPTIME": 0},
        {"DATE_OBS": "2026-11-24T03:15:00"},
        {"DATE_OBS": "yesterday", "EXPTIME": 10},
    ],
)
def test_bad_headers(tmp_path: Path, cards: dict[str, str | float]) -> None:
    path = write_fits(tmp_path / "a.fits", **cards)
    with pytest.raises(HeaderError):
        read_frame_info(path)


def test_not_fits(tmp_path: Path) -> None:
    path = tmp_path / "a.fits"
    path.write_text("not a fits file")
    with pytest.raises(HeaderError, match="FITS"):
        read_frame_info(path)


def test_lcftp_stamped(tmp_path: Path) -> None:
    path = write_fits(
        tmp_path / "a.fits",
        DATE_OBS="2026-11-24T03:15:00",
        EXPTIME=10,
        LCFTPPLN="x",
    )
    assert read_frame_info(path).lcftp_stamped


def test_metadata(tmp_path: Path) -> None:
    path = write_fits(
        tmp_path / "a.fits",
        DATE_OBS="2026-11-24T03:15:00",
        EXPTIME=10,
        TELESCOP="C8",
    )
    assert read_frame_info(path).metadata == {"TELESCOP": "C8"}
