import asyncio
import contextlib
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib.metadata import entry_points
from pathlib import Path
from uuid import UUID

import pytest
from astropy.io import fits
from astropy.wcs import WCS
from tests_support import DEVICE, write_frame

from lcftp_client.clock import FakeClock
from lcftp_client.drivers.base import DriverError, ObservationContext
from lcftp_client.drivers.testing import assert_driver_contract
from lcftp_driver_seestar.config import SeestarDriverConfig
from lcftp_driver_seestar.control import (
    DeviceInfo,
    GotoResult,
    SeestarControlError,
    StackInfo,
)
from lcftp_driver_seestar.driver import (
    SeestarDriver,
    create_driver,
    exposure_plan,
)
from lcftp_models.candidate import BrokerRef, Candidate
from lcftp_models.plan import ExposureHint, PlanItem

NIGHT = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)  # 11pm in Utah
NOON = datetime(2026, 11, 15, 19, 0, tzinfo=UTC)
CONFIG = SeestarDriverConfig(site_lat_deg=40.0, site_lon_deg=-111.0)
RA_DEG, DEC_DEG = 82.5, -5.4


def _item(
    *, minutes: float = 30, hint: ExposureHint | None = None
) -> PlanItem:
    candidate = Candidate(
        dia_object_id=170587116824690819,
        sources=[BrokerRef(broker="alerce", broker_object_id="1")],
        latest_alert_id="42",
        latest_alert_time=NIGHT,
        ra=RA_DEG,
        dec=DEC_DEG,
        mag=13.0,
        band="r",
        first_seen=NIGHT,
        last_updated=NIGHT,
    )
    return PlanItem(
        item_id="item/1",
        candidate=candidate,
        not_before=NIGHT,
        not_after=NIGHT + timedelta(minutes=minutes),
        suggested_filter="r",
        exposure_hint=hint or ExposureHint(total_exposure_s=30),
        priority=0,
    )


def _ctx(tmp_path: Path, when: datetime = NIGHT) -> ObservationContext:
    def new_path(frame_id: UUID) -> Path:
        return tmp_path / f"{frame_id}.fits"

    return ObservationContext(
        plan_id="plan1",
        observatory_id="obs1",
        fov_arcmin=180,
        new_frame_path=new_path,
        clock=FakeClock(when),
    )


class DriverFake:
    """A scriptable SeestarControl that records the calls it gets."""

    def __init__(
        self,
        *,
        stack: list[StackInfo | Exception] | None = None,
        goto: GotoResult | None = None,
        connect_error: Exception | None = None,
        frame_wcs: bool = True,
        on_poll: Callable[[], object] | None = None,
        device: DeviceInfo = DEVICE,
        start_error: Exception | None = None,
        goto_gate: threading.Event | None = None,
        on_goto: Callable[[], object] | None = None,
    ) -> None:
        self.device = device
        self.start_error = start_error
        self.goto_gate = goto_gate
        self.on_goto = on_goto
        self.calls: list[str] = []
        self.on_poll = on_poll
        self.stack = stack or [
            StackInfo(stacked_frames=n, dropped_frames=0, running=True)
            for n in (1, 2, 3)
        ]
        self.goto_result = goto or GotoResult(
            state="complete",
            ra_hours=RA_DEG / 15,
            dec_deg=DEC_DEG + 0.01,
            dist_deg=0.01,
            error=None,
        )
        self.connect_error = connect_error
        self.frame_wcs = frame_wcs
        self.stacked = 0
        self.target = ""

    def connect(self, host: str | None, key_path: Path | None) -> DeviceInfo:
        self.calls.append("connect")
        if self.connect_error:
            raise self.connect_error
        return self.device

    def open_arm(self) -> None:
        self.calls.append("open_arm")

    def goto(
        self,
        target_name: str,
        ra_hours: float,
        dec_deg: float,
        timeout_s: float,
    ) -> GotoResult:
        self.calls.append("goto")
        self.target = target_name
        if self.on_goto:
            self.on_goto()
        if self.goto_gate:
            self.goto_gate.wait(5)
            self.calls.append("goto-done")
        return self.goto_result

    def set_exposure(self, seconds: int) -> None:
        self.calls.append(f"set_exposure:{seconds}")

    def set_save_subframes(self, *, enabled: bool) -> None:
        self.calls.append(f"set_save_subframes:{enabled}")

    def start_stack(self) -> None:
        self.calls.append("start_stack")
        if self.start_error:
            raise self.start_error

    def stack_info(self) -> StackInfo:
        self.calls.append("stack_info")
        if self.on_poll:
            self.on_poll()
        value = self.stack.pop(0) if len(self.stack) > 1 else self.stack[0]
        if isinstance(value, Exception):
            raise value
        self.stacked = value.stacked_frames
        return value

    def stop_stack(self) -> None:
        self.calls.append("stop_stack")

    def list_subframes(self, target_name: str) -> list[str]:
        self.calls.append("list_subframes")
        return [
            f"Light_{target_name}_10.0s_IRCUT_20261115-0600{n:02d}.fit"
            for n in range(self.stacked)
        ]

    def download(self, target_name: str, name: str, dest: Path) -> Path:
        self.calls.append("download")
        write_frame(dest, wcs=self.frame_wcs)
        return dest

    def abort(self) -> None:
        self.calls.append("abort")

    def park(self) -> None:
        self.calls.append("park")

    def disconnect(self) -> None:
        self.calls.append("disconnect")


def _driver(
    fake: DriverFake, config: SeestarDriverConfig = CONFIG
) -> SeestarDriver:
    return SeestarDriver(config, control=fake)


@pytest.mark.filterwarnings("ignore::astropy.wcs.FITSFixedWarning")
async def test_contract(tmp_path: Path) -> None:
    fake = DriverFake()
    result = await assert_driver_contract(
        _driver(fake), _item(), _ctx(tmp_path)
    )
    assert len(result.frames) == 3
    assert fake.calls[:7] == [
        "connect",
        "open_arm",
        "goto",
        "set_exposure:10",
        "set_save_subframes:True",
        "start_stack",
        "stack_info",
    ]


async def test_frames_carry_pointing_and_metadata(tmp_path: Path) -> None:
    fake = DriverFake()
    driver = _driver(fake)
    await driver.connect()
    result = await driver.observe(_item(), _ctx(tmp_path))
    frame = result.frames[0]
    assert frame.exposure_s == 10.0
    assert frame.filter == "IRCUT"
    assert frame.obs_start == datetime(2026, 11, 15, 3, 10, tzinfo=UTC)
    assert (frame.ra, frame.dec) == (82.5, -5.4)
    assert frame.driver_metadata["product_model"] == "Seestar S30 Plus"
    assert frame.driver_metadata["firmware"] == "9.75"
    assert frame.driver_metadata["wcs"] == "device"
    assert frame.driver_metadata["dist_deg"] == 0.01
    assert fake.target == "lcftp_item_1"


async def test_refuses_goto_with_sun_up(tmp_path: Path) -> None:
    fake = DriverFake()
    driver = _driver(fake)
    await driver.connect()
    with pytest.raises(DriverError, match="Sun"):
        await driver.observe(_item(), _ctx(tmp_path, NOON))
    assert fake.calls == ["connect"]


async def test_failed_goto_raises_without_stacking(tmp_path: Path) -> None:
    fake = DriverFake(
        goto=GotoResult(
            state="timeout",
            ra_hours=None,
            dec_deg=None,
            dist_deg=None,
            error="slew took too long",
        )
    )
    driver = _driver(fake)
    await driver.connect()
    with pytest.raises(DriverError, match="timeout"):
        await driver.observe(_item(), _ctx(tmp_path))
    assert "start_stack" not in fake.calls
    assert fake.calls[-1] == "abort"


async def test_large_pointing_error_raises(tmp_path: Path) -> None:
    fake = DriverFake(
        goto=GotoResult(
            state="complete",
            ra_hours=RA_DEG / 15,
            dec_deg=DEC_DEG + 1,
            dist_deg=1.0,  # more than 0.25 of a 3 degree field
            error=None,
        )
    )
    driver = _driver(fake)
    await driver.connect()
    with pytest.raises(DriverError, match="pointing"):
        await driver.observe(_item(), _ctx(tmp_path))
    assert "start_stack" not in fake.calls


async def test_not_after_stops_stack_and_returns_frames(
    tmp_path: Path,
) -> None:
    progress = [
        StackInfo(stacked_frames=n, dropped_frames=0, running=True)
        for n in (0, 1, 1, 2, 2, 2, 2, 2, 2, 2)
    ]
    fake = DriverFake(stack=list(progress))
    driver = _driver(fake)
    await driver.connect()
    item = _item(minutes=0.5, hint=ExposureHint(total_exposure_s=60))
    result = await driver.observe(item, _ctx(tmp_path))
    assert len(result.frames) == 2
    assert result.weather_blocked is False
    assert fake.calls.count("stop_stack") == 1
    assert fake.calls.index("stop_stack") < fake.calls.index("list_subframes")


async def test_stop_stack_called_when_poll_raises(tmp_path: Path) -> None:
    error = SeestarControlError("socket closed")
    fake = DriverFake(stack=[error, error])
    driver = _driver(fake)
    await driver.connect()
    with pytest.raises(DriverError, match="socket closed"):
        await driver.observe(_item(), _ctx(tmp_path))
    assert "stop_stack" in fake.calls


async def test_one_failed_poll_is_retried(tmp_path: Path) -> None:
    done = StackInfo(stacked_frames=3, dropped_frames=0, running=True)
    fake = DriverFake(stack=[SeestarControlError("blip"), done])
    driver = _driver(fake)
    await driver.connect()
    result = await driver.observe(_item(), _ctx(tmp_path))
    assert len(result.frames) == 3


async def test_all_frames_dropped_is_weather_blocked(tmp_path: Path) -> None:
    clouds = StackInfo(stacked_frames=0, dropped_frames=4, running=True)
    fake = DriverFake(stack=[clouds])
    driver = _driver(fake)
    await driver.connect()
    result = await driver.observe(_item(minutes=1), _ctx(tmp_path))
    assert result.frames == []
    assert result.weather_blocked is True
    assert "stop_stack" in fake.calls


async def test_nothing_taken_at_all_is_an_error(tmp_path: Path) -> None:
    idle = StackInfo(stacked_frames=0, dropped_frames=0, running=False)
    driver = _driver(DriverFake(stack=[idle]))
    await driver.connect()
    with pytest.raises(DriverError, match="no sub-frames"):
        await driver.observe(_item(minutes=1), _ctx(tmp_path))


async def test_cancelled_observe_stops_stack(tmp_path: Path) -> None:
    waiting = StackInfo(stacked_frames=0, dropped_frames=0, running=True)
    loop = asyncio.get_running_loop()
    polled = asyncio.Event()
    fake = DriverFake(
        stack=[waiting],
        on_poll=lambda: loop.call_soon_threadsafe(polled.set),
    )
    driver = _driver(fake)
    await driver.connect()
    task = asyncio.create_task(
        driver.observe(_item(minutes=600), _ctx(tmp_path))
    )
    await asyncio.wait_for(polled.wait(), timeout=5)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert fake.calls[-1] == "stop_stack"


async def test_missing_key_on_new_firmware_fails_connect() -> None:
    error = SeestarControlError("set key_path or SEESTAR_KEY_PATH")
    driver = _driver(DriverFake(connect_error=error))
    with pytest.raises(DriverError, match="SEESTAR_KEY_PATH"):
        await driver.connect()


async def test_observe_before_connect_raises(tmp_path: Path) -> None:
    with pytest.raises(DriverError, match="connect"):
        await _driver(DriverFake()).observe(_item(), _ctx(tmp_path))


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        (ExposureHint(total_exposure_s=60), 6),
        (ExposureHint(total_exposure_s=3), 1),
        (ExposureHint(total_exposure_s=61), 7),
        (ExposureHint(target_snr=20), 6),
    ],
)
def test_exposure_plan(hint: ExposureHint, expected: int) -> None:
    assert exposure_plan(hint, 10, 60) == expected


@pytest.mark.filterwarnings("ignore::astropy.wcs.FITSFixedWarning")
async def test_wcs_added_when_header_lacks_one(tmp_path: Path) -> None:
    fake = DriverFake(frame_wcs=False)
    driver = _driver(fake)
    await driver.connect()
    result = await driver.observe(_item(), _ctx(tmp_path))
    frame = result.frames[0]
    assert frame.driver_metadata["wcs"] == "approximate-pointing"
    with fits.open(frame.path) as hdul:
        wcs = WCS(hdul[0].header)
    assert wcs.has_celestial
    scale_arcsec = abs(wcs.wcs.cdelt[0]) * 3600
    assert scale_arcsec == pytest.approx(206.265 * 2.9 / 160.0)
    center = wcs.pixel_to_world(719.5, 1279.5)
    assert center.ra.deg == pytest.approx(RA_DEG, abs=1e-6)
    assert center.dec.deg == pytest.approx(DEC_DEG + 0.01, abs=1e-6)


async def test_park_is_idempotent_and_safe_before_connect() -> None:
    fake = DriverFake()
    driver = _driver(fake)
    await driver.park()
    assert fake.calls == []
    await driver.connect()
    await driver.park()
    await driver.park()
    assert fake.calls == ["connect", "abort", "park", "abort", "park"]


def test_create_driver_from_config_mapping() -> None:
    driver = create_driver({"site_lat_deg": 40.0, "site_lon_deg": -111.0})
    assert isinstance(driver, SeestarDriver)
    assert driver.name == "seestar"
    assert "seestarpy" in driver.version


def test_entry_point_loads_factory() -> None:
    (ep,) = [
        ep
        for ep in entry_points(group="lcftp.drivers")
        if ep.name == "seestar"
    ]
    assert ep.load() is create_driver


async def test_start_stack_failure_still_stops(tmp_path: Path) -> None:
    fake = DriverFake(start_error=SeestarControlError("reply lost"))
    driver = _driver(fake)
    await driver.connect()
    with pytest.raises(DriverError, match="reply lost"):
        await driver.observe(_item(), _ctx(tmp_path))
    assert fake.calls[-1] == "stop_stack"


async def test_cancel_during_goto_waits_for_the_slew(tmp_path: Path) -> None:
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    gate = threading.Event()
    fake = DriverFake(
        goto_gate=gate,
        on_goto=lambda: loop.call_soon_threadsafe(entered.set),
    )
    driver = _driver(fake)
    await driver.connect()
    task = asyncio.create_task(driver.observe(_item(), _ctx(tmp_path)))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    park = asyncio.create_task(driver.park())
    await asyncio.sleep(0.05)
    assert "park" not in fake.calls  # still waiting for the slew thread
    gate.set()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await asyncio.wait_for(park, timeout=5)
    assert fake.calls[-4:] == ["goto", "goto-done", "abort", "park"]


async def test_site_mismatch_fails_connect() -> None:
    utah_as_west_positive = CONFIG.model_copy(update={"site_lon_deg": 111.0})
    driver = _driver(DriverFake(), utah_as_west_positive)
    with pytest.raises(DriverError, match="east"):
        await driver.connect()


async def test_device_without_site_connects() -> None:
    device = DEVICE.model_copy(update={"site_lon_lat": None})
    await _driver(DriverFake(device=device)).connect()
