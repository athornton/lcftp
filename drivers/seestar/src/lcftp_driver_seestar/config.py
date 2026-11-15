"""Configuration of the Seestar driver."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["SeestarDriverConfig", "SubExposure"]

type SubExposure = Literal[2, 5, 10, 20, 30, 60]
"""Sub-frame exposure times (seconds) the Seestar firmware accepts."""


class SeestarDriverConfig(BaseModel):
    """How to reach and operate one Seestar."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    site_lat_deg: Annotated[float, Field(ge=-90, le=90)]
    site_lon_deg: Annotated[float, Field(ge=-180, le=360)]
    site_elevation_m: float = 0
    host: str | None = None
    """Telescope address; `None` uses seestarpy's discovery."""

    key_path: Path | None = None
    """Authentication key (PEM) required by firmware 7.18 and later."""

    sub_exposure_s: SubExposure = 10
    default_total_exposure_s: Annotated[float, Field(gt=0)] = 60
    goto_timeout_s: Annotated[float, Field(gt=0)] = 180
    max_pointing_error_fov: Annotated[float, Field(gt=0)] = 0.25
    poll_interval_s: Annotated[float, Field(gt=0)] = 5
    sun_altitude_limit_deg: Annotated[float, Field(ge=-90, le=90)] = -6
