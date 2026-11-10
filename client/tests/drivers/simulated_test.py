import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from lcftp_client.clock import FakeClock
from lcftp_client.drivers.base import DriverError, ObservationContext
from lcftp_client.drivers.simulated import (
    SimulatedDriver,
    SimulatedDriverConfig,
)
from lcftp_client.drivers.testing import assert_driver_contract
from lcftp_models.plan import ObservationPlan

T0 = datetime(2026, 11, 10, 3, tzinfo=UTC)


def _ctx(tmp_path: Path) -> ObservationContext:
    def new_path(frame_id: UUID) -> Path:
        return tmp_path / f"{frame_id}.fits"

    return ObservationContext(
        plan_id="plan1",
        observatory_id="obs1",
        fov_arcmin=60,
        new_frame_path=new_path,
        clock=FakeClock(T0),
    )


async def test_contract(tmp_path: Path, plan: ObservationPlan) -> None:
    driver = SimulatedDriver(SimulatedDriverConfig())
    await assert_driver_contract(driver, plan.items[0], _ctx(tmp_path))


async def test_target_is_bright(tmp_path: Path, plan: ObservationPlan) -> None:
    item = plan.items[0]
    driver = SimulatedDriver(SimulatedDriverConfig(seed=3))
    result = await driver.observe(item, _ctx(tmp_path))
    [frame] = result.frames
    with fits.open(frame.path) as hdul:
        data = hdul[0].data.astype(float)
        header = hdul[0].header
    x, y = WCS(header).world_to_pixel_values(
        item.candidate.ra, item.candidate.dec
    )
    peak = data[round(float(y)), round(float(x))]
    assert peak > np.median(data) + 5 * np.std(data[:20, :20])
    assert header["EXPTIME"] == 300
    assert header["FILTER"] == "r"
    assert header["DATE-OBS"].startswith("2026-11-10T03:00:00")
    assert frame.ra == item.candidate.ra


async def test_seed_reproducible(
    tmp_path: Path, plan: ObservationPlan
) -> None:
    item = plan.items[0]
    a = await SimulatedDriver(SimulatedDriverConfig(seed=5)).observe(
        item, _ctx(tmp_path)
    )
    b = await SimulatedDriver(SimulatedDriverConfig(seed=5)).observe(
        item, _ctx(tmp_path)
    )
    assert a.frames[0].path.read_bytes() == b.frames[0].path.read_bytes()


async def test_failure(tmp_path: Path, plan: ObservationPlan) -> None:
    driver = SimulatedDriver(SimulatedDriverConfig(failure_rate=1))
    with pytest.raises(DriverError):
        await driver.observe(plan.items[0], _ctx(tmp_path))


async def test_clouds(tmp_path: Path, plan: ObservationPlan) -> None:
    driver = SimulatedDriver(SimulatedDriverConfig(cloud_rate=1))
    result = await driver.observe(plan.items[0], _ctx(tmp_path))
    assert result.weather_blocked
    assert result.frames == []


async def test_timeout_hangs(tmp_path: Path, plan: ObservationPlan) -> None:
    driver = SimulatedDriver(SimulatedDriverConfig(timeout_rate=1))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            driver.observe(plan.items[0], _ctx(tmp_path)), 0.1
        )


async def test_frames_per_item(tmp_path: Path, plan: ObservationPlan) -> None:
    driver = SimulatedDriver(
        SimulatedDriverConfig(frames_per_item=3, image_size=32)
    )
    result = await driver.observe(plan.items[0], _ctx(tmp_path))
    assert len(result.frames) == 3
    assert len({f.frame_id for f in result.frames}) == 3
