"""Contract check for telescope driver plugins."""

from astropy.io import fits
from astropy.wcs import WCS

from lcftp_models.plan import PlanItem

from .base import DriverResult, ObservationContext, TelescopeDriver

__all__ = ["assert_driver_contract"]


async def assert_driver_contract(
    driver: TelescopeDriver, item: PlanItem, ctx: ObservationContext
) -> DriverResult:
    """Exercise a driver and assert the `TelescopeDriver` contract."""
    assert driver.name, "driver must have a name"
    assert driver.version, "driver must have a version"
    await driver.connect()
    result = await driver.observe(item, ctx)
    for frame in result.frames:
        assert frame.path == ctx.new_frame_path(frame.frame_id), frame
        with fits.open(frame.path) as hdul:
            header = hdul[0].header
            assert hdul[0].data is not None
        assert WCS(header).has_celestial, "frames need a celestial WCS"
        assert frame.exposure_s > 0
    await driver.park()
    await driver.park()
    await driver.disconnect()
    return result
