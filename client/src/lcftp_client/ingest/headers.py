"""Read what ingest needs from an observer's FITS headers."""

from datetime import UTC
from pathlib import Path
from typing import Annotated, Any

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

__all__ = ["FrameInfo", "HeaderError", "read_frame_info"]

METADATA_KEYWORDS = ("TELESCOP", "INSTRUME", "OBSERVER")
"""Header keywords copied into ``FrameRecord.driver_metadata``."""


class HeaderError(ValueError):
    """A frame's headers lack something ingest needs."""


class FrameInfo(BaseModel):
    """What a frame's headers say about it."""

    model_config = ConfigDict(frozen=True)

    obs_start: AwareDatetime
    exposure_s: Annotated[float, Field(gt=0)]
    filter: str | None
    ra: float | None
    """Pointing right ascension in degrees, if the headers give it."""

    dec: float | None
    """Pointing declination in degrees, if the headers give it."""

    object: str | None
    lcftp_stamped: bool
    """Whether the frame already carries lcftp provenance."""

    metadata: dict[str, JsonValue]


def _text(header: Any, key: str) -> str | None:
    value = header.get(key)
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _obs_start(header: Any) -> AwareDatetime:
    value = _text(header, "DATE-OBS")
    if value is None:
        msg = "no DATE-OBS"
        raise HeaderError(msg)
    try:
        return Time(value, scale="utc").to_datetime(timezone=UTC)
    except ValueError as e:
        msg = f"unreadable DATE-OBS {value!r}"
        raise HeaderError(msg) from e


def _exposure(header: Any) -> float:
    for key in ("EXPTIME", "EXPOSURE"):
        if key in header:
            try:
                value = float(header[key])
            except (TypeError, ValueError) as e:
                msg = f"unreadable {key} {header[key]!r}"
                raise HeaderError(msg) from e
            if value <= 0:
                msg = f"{key} must be positive, got {value}"
                raise HeaderError(msg)
            return value
    msg = "no EXPTIME or EXPOSURE"
    raise HeaderError(msg)


def _pointing(header: Any) -> tuple[float | None, float | None]:
    if "RA" in header and "DEC" in header:
        try:
            return float(header["RA"]), float(header["DEC"])
        except (TypeError, ValueError):
            pass
    ra, dec = _text(header, "OBJCTRA"), _text(header, "OBJCTDEC")
    if ra is None or dec is None:
        return None, None
    try:
        coord = SkyCoord(ra, dec, unit=(u.hourangle, u.deg))
    except ValueError:
        return None, None
    return float(coord.ra.deg), float(coord.dec.deg)


def read_frame_info(path: Path) -> FrameInfo:
    """Read a frame's primary header.

    Raises
    ------
    HeaderError
        If the file is not readable FITS, or lacks a usable ``DATE-OBS``
        or exposure time.
    """
    try:
        header = fits.getheader(path)
    except (OSError, ValueError) as e:
        msg = "not a readable FITS file"
        raise HeaderError(msg) from e
    ra, dec = _pointing(header)
    return FrameInfo(
        obs_start=_obs_start(header),
        exposure_s=_exposure(header),
        filter=_text(header, "FILTER"),
        ra=ra,
        dec=dec,
        object=_text(header, "OBJECT"),
        lcftp_stamped="LCFTPPLN" in header,
        metadata={
            key: str(header[key])
            for key in METADATA_KEYWORDS
            if _text(header, key) is not None
        },
    )
