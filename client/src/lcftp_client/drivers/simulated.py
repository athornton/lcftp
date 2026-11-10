"""A simulated telescope producing plausible FITS images."""

import asyncio
import random
from collections.abc import Mapping
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from lcftp_models.plan import PlanItem

from .base import CapturedFrame, DriverError, DriverResult, ObservationContext

__all__ = ["SimulatedDriver", "SimulatedDriverConfig", "create_driver"]

_BACKGROUND = 100.0
_NOISE = 10.0
_PSF_SIGMA = 2.0
_MAX_PEAK = 30000.0
_FIELD_STARS = 20
_DEFAULT_EXPOSURE_S = 10.0

type Rate = Annotated[float, Field(ge=0, le=1)]


class SimulatedDriverConfig(BaseModel):
    """Behaviour of the simulated telescope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seed: int = 0
    failure_rate: Rate = 0
    timeout_rate: Rate = 0
    cloud_rate: Rate = 0
    image_size: Annotated[int, Field(ge=16, le=4096)] = 256
    frames_per_item: Annotated[int, Field(ge=1)] = 1


def _gaussian(
    image: np.ndarray, x: float, y: float, peak: float, sigma: float
) -> None:
    yy, xx = np.indices(image.shape)
    image += peak * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))


class SimulatedDriver:
    """Telescope stand-in that never sleeps for its exposures."""

    def __init__(self, config: SimulatedDriverConfig) -> None:
        self.config = config
        self._faults = random.Random(config.seed)  # noqa: S311
        self._rng = np.random.default_rng(config.seed)
        self.parked = False

    @property
    def name(self) -> str:
        """Driver name."""
        return "simulated"

    @property
    def version(self) -> str:
        """Version of lcftp-client."""
        return version("lcftp-client")

    async def connect(self) -> None:
        """Pretend to connect."""
        self.parked = False

    async def disconnect(self) -> None:
        """Pretend to disconnect."""

    async def park(self) -> None:
        """Pretend to park."""
        self.parked = True

    async def observe(
        self, item: PlanItem, ctx: ObservationContext
    ) -> DriverResult:
        """Simulate observing a plan item, with optional faults."""
        roll = self._faults.random()
        c = self.config
        if roll < c.failure_rate:
            msg = "Simulated mount failure"
            raise DriverError(msg)
        if roll < c.failure_rate + c.timeout_rate:
            await asyncio.Event().wait()
        if roll < c.failure_rate + c.timeout_rate + c.cloud_rate:
            return DriverResult(frames=[], weather_blocked=True)

        exposure = item.exposure_hint.total_exposure_s or _DEFAULT_EXPOSURE_S
        frames = []
        for _ in range(c.frames_per_item):
            frame_id = uuid4()
            path = ctx.new_frame_path(frame_id)
            start = ctx.clock.now()
            image = self._image(item, ctx.fov_arcmin)
            header = self._header(item, ctx.fov_arcmin, start, exposure)
            await asyncio.to_thread(_write, path, image, header)
            frames.append(
                CapturedFrame(
                    frame_id=frame_id,
                    path=path,
                    obs_start=start,
                    exposure_s=exposure,
                    filter=item.suggested_filter,
                    ra=item.candidate.ra,
                    dec=item.candidate.dec,
                    driver_metadata={"simulated": True, "seed": c.seed},
                )
            )
        return DriverResult(frames=frames)

    def _image(self, item: PlanItem, fov_arcmin: float) -> np.ndarray:
        n = self.config.image_size
        image = self._rng.normal(_BACKGROUND, _NOISE, size=(n, n))
        for _ in range(_FIELD_STARS):
            x, y = self._rng.uniform(0, n, size=2)
            _gaussian(image, x, y, self._rng.uniform(50, 5000), _PSF_SIGMA)
        peak = min(_MAX_PEAK, 200 * 10 ** (-0.4 * (item.candidate.mag - 15)))
        _gaussian(image, (n - 1) / 2, (n - 1) / 2, peak, _PSF_SIGMA)
        return np.clip(image, 0, 32767).astype(np.int16)

    def _header(
        self,
        item: PlanItem,
        fov_arcmin: float,
        start: datetime,
        exposure: float,
    ) -> fits.Header:
        n = self.config.image_size
        scale_deg = fov_arcmin / 60 / n
        wcs = WCS(naxis=2)
        wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        wcs.wcs.crval = [item.candidate.ra, item.candidate.dec]
        wcs.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
        wcs.wcs.cdelt = [-scale_deg, scale_deg]
        header = wcs.to_header()
        header["DATE-OBS"] = start.strftime("%Y-%m-%dT%H:%M:%S.%f")
        header["EXPTIME"] = exposure
        header["FILTER"] = item.suggested_filter or ""
        header["OBJECT"] = str(item.candidate.dia_object_id)
        header["TELESCOP"] = "LCFTP simulated telescope"
        return header


def _write(path: Path, image: np.ndarray, header: fits.Header) -> None:
    fits.PrimaryHDU(image, header=header).writeto(path)


def create_driver(config: Mapping[str, JsonValue]) -> SimulatedDriver:
    """Create the simulated driver from the client's driver_config."""
    return SimulatedDriver(SimulatedDriverConfig.model_validate(dict(config)))
