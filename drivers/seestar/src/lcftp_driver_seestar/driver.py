"""The Seestar telescope driver."""

import asyncio
import contextlib
import logging
import math
import re
import warnings
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from pydantic import JsonValue

from lcftp_client.drivers.base import (
    CapturedFrame,
    DriverError,
    DriverResult,
    ObservationContext,
)
from lcftp_models.plan import ExposureHint, PlanItem

from .config import SeestarDriverConfig
from .control import (
    DeviceInfo,
    GotoResult,
    SeestarControl,
    SeestarControlError,
    SeestarPyControl,
    StackInfo,
    angular_distance_deg,
)
from .safety import sun_altitude_deg

__all__ = ["SeestarDriver", "create_driver", "exposure_plan"]

logger = logging.getLogger(__name__)

_SITE_TOLERANCE_DEG = 1.0
_ARCSEC_PER_RADIAN_MM_PER_UM = 206.265
"""Plate scale (arcsec/px) = this * pixel size (µm) / focal length (mm)."""


def exposure_plan(
    hint: ExposureHint, sub_exposure_s: int, default_total_s: float
) -> int:
    """Return how many sub-frames meet an exposure hint."""
    total = hint.total_exposure_s or default_total_s
    return max(1, math.ceil(total / sub_exposure_s))


def _target_name(item: PlanItem) -> str:
    """Name the on-device target (and so its folder) after the plan item."""
    return "lcftp_" + re.sub(r"[^A-Za-z0-9_-]", "_", item.item_id)


def _package_version(name: str) -> str:
    try:
        return version(name)
    except Exception:  # pragma: no cover
        return "unknown"


def _celestial(header: fits.Header) -> bool:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        return bool(WCS(header).has_celestial)


def _obs_start(header: fits.Header, fallback: datetime) -> datetime:
    value = header.get("DATE-OBS")
    if not isinstance(value, str):
        return fallback
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return fallback
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _pointing(header: fits.Header, goto: GotoResult) -> tuple[float, float]:
    """RA/Dec (degrees) of the frame: WCS centre, header, or the mount."""
    for ra_key, dec_key in (("CRVAL1", "CRVAL2"), ("RA", "DEC")):
        ra, dec = header.get(ra_key), header.get(dec_key)
        if isinstance(ra, (int, float)) and isinstance(dec, (int, float)):
            return float(ra), float(dec)
    if goto.ra_hours is None or goto.dec_deg is None:
        msg = "no pointing in frame header or goto result"
        raise DriverError(msg)
    return goto.ra_hours * 15, goto.dec_deg


class SeestarDriver:
    """Run LCFTP plan items on a ZWO Seestar."""

    def __init__(
        self,
        config: SeestarDriverConfig,
        control: SeestarControl | None = None,
    ) -> None:
        self.config = config
        self._control: SeestarControl = control or SeestarPyControl()
        self._lock = asyncio.Lock()
        self._device: DeviceInfo | None = None
        self._arm_open = False

    @property
    def name(self) -> str:
        """Entry-point name of the driver."""
        return "seestar"

    @property
    def version(self) -> str:
        """Driver and seestarpy versions, recorded in every frame."""
        return (
            f"{_package_version('lcftp-driver-seestar')}"
            f"+seestarpy{_package_version('seestarpy')}"
        )

    async def _run(
        self, call: Callable[..., Any], *args: Any, **kw: Any
    ) -> Any:
        """Run a blocking control call in a thread, one at a time.

        If the caller is cancelled, the lock is held until the thread has
        actually finished, so the next command (often ``park``) cannot
        reach the telescope while a slew is still in progress.
        """
        async with self._lock:
            future = asyncio.ensure_future(
                asyncio.to_thread(call, *args, **kw)
            )
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await future
                raise
            except SeestarControlError as e:
                raise DriverError(str(e)) from e

    async def connect(self) -> None:
        """Connect to the telescope and identify it."""
        self._device = await self._run(
            self._control.connect, self.config.host, self.config.key_path
        )
        self._arm_open = False
        self._check_site(self._device)
        logger.info(
            "Connected to %s (firmware %s)",
            self._device.product_model,
            self._device.firmware,
        )

    async def disconnect(self) -> None:
        """Disconnect from the telescope."""
        if self._device is None:
            return
        try:
            await self._run(self._control.disconnect)
        finally:
            self._device = None
            self._arm_open = False

    async def park(self) -> None:
        """Stop any stack and park; safe at any time, repeatable."""
        if self._device is None:
            return
        with contextlib.suppress(DriverError):
            await self._run(self._control.abort)
        await self._run(self._control.park)
        self._arm_open = False

    async def observe(
        self, item: PlanItem, ctx: ObservationContext
    ) -> DriverResult:
        """Slew to the item's target, stack sub-frames and download them."""
        device = self._device
        if device is None:
            msg = "observe called before connect"
            raise DriverError(msg)
        self._check_sun(ctx.clock.now())
        if not self._arm_open:
            await self._run(self._control.open_arm)
            self._arm_open = True
        target = _target_name(item)
        goto = await self._goto(target, item, ctx)
        wanted = exposure_plan(
            item.exposure_hint,
            self.config.sub_exposure_s,
            self.config.default_total_exposure_s,
        )
        await self._run(self._control.set_exposure, self.config.sub_exposure_s)
        await self._run(self._control.set_save_subframes, enabled=True)
        try:
            await self._run(self._control.start_stack)
            info = await self._wait_for_frames(wanted, item, ctx)
        finally:
            try:
                await self._run(self._control.stop_stack)
            except DriverError as e:
                logger.warning("Could not stop Seestar stack: %s", e)
        if info.stacked_frames == 0:
            if info.dropped_frames > 0:
                return DriverResult(frames=[], weather_blocked=True)
            msg = f"no sub-frames were taken for {item.item_id}"
            raise DriverError(msg)
        names = await self._run(self._control.list_subframes, target)
        frames = []
        for name in names:
            frame_id = uuid4()
            path = ctx.new_frame_path(frame_id)
            await self._run(self._control.download, target, name, path)
            frames.append(
                self._frame(
                    frame_id,
                    path,
                    device=device,
                    goto=goto,
                    info=info,
                    now=ctx.clock.now,
                )
            )
        return DriverResult(frames=frames)

    def _check_site(self, device: DeviceInfo) -> None:
        """Refuse a configured site far from where the telescope says it is.

        The site drives the Sun safety check, so a sign error in longitude
        (west entered as positive) must not pass silently.
        """
        if device.site_lon_lat is None:
            return
        lon, lat = device.site_lon_lat
        distance = angular_distance_deg(
            self.config.site_lon_deg / 15,
            self.config.site_lat_deg,
            lon / 15,
            lat,
        )
        if distance > _SITE_TOLERANCE_DEG:
            msg = (
                f"Configured site (lat {self.config.site_lat_deg}, lon"
                f" {self.config.site_lon_deg}) is {distance:.1f}° from the"
                f" telescope's own location (lat {lat}, lon {lon});"
                " site_lon_deg is east-positive (west is negative)"
            )
            raise DriverError(msg)

    def _check_sun(self, when: datetime) -> None:
        altitude = sun_altitude_deg(
            when,
            lat_deg=self.config.site_lat_deg,
            lon_deg=self.config.site_lon_deg,
        )
        if altitude > self.config.sun_altitude_limit_deg:
            msg = (
                f"Refusing to slew: the Sun is at {altitude:.1f}° (limit"
                f" {self.config.sun_altitude_limit_deg}°)"
            )
            raise DriverError(msg)

    async def _goto(
        self, target: str, item: PlanItem, ctx: ObservationContext
    ) -> GotoResult:
        goto: GotoResult = await self._run(
            self._control.goto,
            target,
            item.candidate.ra / 15,
            item.candidate.dec,
            self.config.goto_timeout_s,
        )
        if goto.state != "complete":
            # The firmware may still be slewing or plate solving.
            with contextlib.suppress(DriverError):
                await self._run(self._control.abort)
            msg = f"goto {goto.state}: {goto.error}"
            raise DriverError(msg)
        limit = self.config.max_pointing_error_fov * ctx.fov_arcmin / 60
        if goto.dist_deg is None or goto.dist_deg > limit:
            msg = (
                f"pointing error {goto.dist_deg}° exceeds {limit:.3f}°"
                f" for {item.item_id}"
            )
            raise DriverError(msg)
        return goto

    async def _poll(self) -> StackInfo:
        """Read stack progress, retrying one failure."""
        try:
            info: StackInfo = await self._run(self._control.stack_info)
        except DriverError as e:
            logger.warning("Seestar stack poll failed, retrying: %s", e)
            info = await self._run(self._control.stack_info)
        return info

    async def _wait_for_frames(
        self, wanted: int, item: PlanItem, ctx: ObservationContext
    ) -> StackInfo:
        interval = timedelta(seconds=self.config.poll_interval_s)
        while True:
            info = await self._poll()
            if info.stacked_frames >= wanted:
                return info
            now = ctx.clock.now()
            if now >= item.not_after:
                logger.info(
                    "Stopping %s at not_after with %d of %d sub-frames",
                    item.item_id,
                    info.stacked_frames,
                    wanted,
                )
                return info
            await ctx.clock.sleep_until(min(now + interval, item.not_after))

    def _frame(
        self,
        frame_id: UUID,
        path: Path,
        *,
        device: DeviceInfo,
        goto: GotoResult,
        info: StackInfo,
        now: Callable[[], datetime],
    ) -> CapturedFrame:
        with fits.open(path, mode="update") as hdul:
            header = hdul[0].header
            ra, dec = _pointing(header, goto)
            if _celestial(header):
                wcs_source = "device"
            else:
                self._add_wcs(header, device, ra, dec)
                wcs_source = "approximate-pointing"
            exptime = header.get("EXPTIME")
            filt = header.get("FILTER")
            obs_start = _obs_start(header, now())
        metadata: dict[str, JsonValue] = {
            "product_model": device.product_model,
            "firmware": device.firmware,
            "dropped_frames": info.dropped_frames,
            "dist_deg": goto.dist_deg,
            "wcs": wcs_source,
        }
        return CapturedFrame(
            frame_id=frame_id,
            path=path,
            obs_start=obs_start,
            exposure_s=(
                float(exptime)
                if isinstance(exptime, (int, float)) and exptime > 0
                else float(self.config.sub_exposure_s)
            ),
            filter=str(filt) if filt else None,
            ra=ra % 360,
            dec=dec,
            driver_metadata=metadata,
        )

    @staticmethod
    def _add_wcs(
        header: fits.Header, device: DeviceInfo, ra: float, dec: float
    ) -> None:
        """Add a TAN WCS centred on the pointing, from the optics."""
        pixel_um = header.get("XPIXSZ")
        if not isinstance(pixel_um, (int, float)) or not device.focal_len_mm:
            msg = "frame has no WCS and no XPIXSZ/focal length to build one"
            raise DriverError(msg)
        scale_deg = (
            _ARCSEC_PER_RADIAN_MM_PER_UM
            * pixel_um
            / device.focal_len_mm
            / 3600
        )
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CRVAL1"] = ra
        header["CRVAL2"] = dec
        header["CRPIX1"] = header["NAXIS1"] / 2 + 0.5
        header["CRPIX2"] = header["NAXIS2"] / 2 + 0.5
        header["CDELT1"] = -scale_deg
        header["CDELT2"] = scale_deg
        header["CUNIT1"] = "deg"
        header["CUNIT2"] = "deg"


def create_driver(config: Mapping[str, JsonValue]) -> SeestarDriver:
    """Create the driver from the client's ``driver_config``."""
    return SeestarDriver(SeestarDriverConfig.model_validate(config))
