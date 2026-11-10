"""Observatory site and capability models."""

from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, Field

from ._base import ContractModel

__all__ = ["FilterSpec", "ObservingConstraints", "SiteLocation"]


def _check_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as e:
        msg = f"Unknown timezone: {value}"
        raise ValueError(msg) from e
    return value


class SiteLocation(ContractModel):
    """Geographic location of an observatory."""

    latitude: Annotated[float, Field(ge=-90, le=90)]
    """Geodetic latitude in degrees, north positive."""

    longitude: Annotated[float, Field(ge=-180, le=180)]
    """Longitude in degrees, east positive."""

    elevation_m: float
    """Elevation above sea level in meters."""

    timezone: Annotated[str, AfterValidator(_check_timezone)]
    """IANA timezone name of the site."""


class FilterSpec(ContractModel):
    """A filter available at an observatory."""

    name: Annotated[str, Field(min_length=1)]
    wavelength_nm: Annotated[float, Field(gt=0)]
    """Effective wavelength in nanometers."""


class ObservingConstraints(ContractModel):
    """Per-observatory limits applied when planning."""

    min_altitude_deg: Annotated[float, Field(ge=0, le=90)] = 30.0
    max_airmass: Annotated[float, Field(ge=1)] = 2.0
    min_moon_separation_deg: Annotated[float, Field(ge=0, le=180)] = 30.0
