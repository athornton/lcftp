from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override
from uuid import uuid4

from astropy.io import fits

from lcftp_client.clock import FakeClock
from lcftp_client.drivers.base import (
    DriverError,
    DriverResult,
    ObservationContext,
)
from lcftp_client.drivers.simulated import (
    SimulatedDriver,
    SimulatedDriverConfig,
)
from lcftp_client.runner import Runner
from lcftp_client.spool import Spool
from lcftp_models.checksums import compute_checksums
from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.plan import ObservationPlan, PlanItem
from lcftp_models.upload import FrameRecord, ItemOutcome, OutcomeReason

T0 = datetime(2026, 11, 10, 3, tzinfo=UTC)


class ScriptedDriver(SimulatedDriver):
    """Simulated driver with per-item scripted behaviour."""

    def __init__(
        self, script: dict[str, Callable[[], object]] | None = None
    ) -> None:
        super().__init__(SimulatedDriverConfig(image_size=32))
        self.script = script or {}
        self.observed: list[str] = []
        self.park_calls = 0

    @override
    async def observe(
        self, item: PlanItem, ctx: ObservationContext
    ) -> DriverResult:
        self.observed.append(item.item_id)
        if action := self.script.get(item.item_id):
            result = action()
            if isinstance(result, DriverResult):
                return result
            if result == "hang":
                await SimulatedDriver(
                    SimulatedDriverConfig(timeout_rate=1)
                ).observe(item, ctx)
        return await super().observe(item, ctx)

    @override
    async def park(self) -> None:
        self.park_calls += 1
        await super().park()


def _runner(
    tmp_path: Path,
    profile: ObservatoryProfile,
    driver: ScriptedDriver,
    clock: FakeClock | None = None,
    timeout: float = 5.0,
) -> tuple[Runner, Spool]:
    spool = Spool(tmp_path / "spool")
    runner = Runner(
        driver,
        spool,
        clock or FakeClock(T0),
        observatory=profile,
        item_timeout_s=timeout,
    )
    return runner, spool


def _reasons(outcomes: list[ItemOutcome]) -> list[tuple[str, str]]:
    return [(o.status, o.reason.value) for o in outcomes]


async def test_all_observed(
    tmp_path: Path, profile: ObservatoryProfile, plan: ObservationPlan
) -> None:
    driver = ScriptedDriver()
    runner, spool = _runner(tmp_path, profile, driver)
    outcomes = await runner.run(plan)
    assert _reasons(outcomes) == [("observed", "ok")] * 3
    frames = spool.pending_frames()
    assert len(frames) == 3
    for record in frames:
        path = spool.frame_path(record.frame_id)
        header = fits.getheader(path)
        assert header["LCFTPPLN"] == "plan1"
        assert header["LCFTPITM"] == record.item_id
        assert compute_checksums(path) == record.checksums
        assert record.size_bytes == path.stat().st_size
        assert record.observatory_id == "obs1"
        assert record.driver == "simulated"
    assert driver.park_calls == 1


async def test_window_passed(
    tmp_path: Path, profile: ObservatoryProfile, plan: ObservationPlan
) -> None:
    clock = FakeClock(T0 + timedelta(minutes=15))
    runner, _ = _runner(tmp_path, profile, ScriptedDriver(), clock)
    outcomes = await runner.run(plan)
    assert _reasons(outcomes) == [
        ("skipped", "window_passed"),
        ("observed", "ok"),
        ("observed", "ok"),
    ]


async def test_driver_failure_does_not_stop_night(
    tmp_path: Path, profile: ObservatoryProfile, plan: ObservationPlan
) -> None:
    def fail() -> None:
        raise DriverError("mount jammed")

    runner, _ = _runner(tmp_path, profile, ScriptedDriver({"001": fail}))
    outcomes = await runner.run(plan)
    assert _reasons(outcomes) == [
        ("observed", "ok"),
        ("failed", "driver_error"),
        ("observed", "ok"),
    ]
    assert outcomes[1].detail == "mount jammed"


async def test_timeout(
    tmp_path: Path, profile: ObservatoryProfile, plan: ObservationPlan
) -> None:
    driver = ScriptedDriver({"000": lambda: "hang"})
    runner, _ = _runner(tmp_path, profile, driver, timeout=0.05)
    outcomes = await runner.run(plan)
    assert _reasons(outcomes)[0] == ("failed", "timeout")
    assert _reasons(outcomes)[1] == ("observed", "ok")


async def test_weather(
    tmp_path: Path, profile: ObservatoryProfile, plan: ObservationPlan
) -> None:
    clouds = DriverResult(frames=[], weather_blocked=True)
    runner, _ = _runner(
        tmp_path, profile, ScriptedDriver({"002": lambda: clouds})
    )
    outcomes = await runner.run(plan)
    assert _reasons(outcomes)[2] == ("skipped", "weather")


async def test_stop(
    tmp_path: Path, profile: ObservatoryProfile, plan: ObservationPlan
) -> None:
    driver = ScriptedDriver()
    runner, _ = _runner(tmp_path, profile, driver)
    driver.script["001"] = runner.request_stop
    outcomes = await runner.run(plan)
    assert _reasons(outcomes) == [
        ("observed", "ok"),
        ("observed", "ok"),
        ("skipped", "stopped"),
    ]
    assert driver.park_calls == 1


async def test_stop_file(
    tmp_path: Path, profile: ObservatoryProfile, plan: ObservationPlan
) -> None:
    driver = ScriptedDriver()
    runner, spool = _runner(tmp_path, profile, driver)
    driver.script["000"] = spool.request_stop
    outcomes = await runner.run(plan)
    assert _reasons(outcomes)[1:] == [("skipped", "stopped")] * 2


async def test_resume(
    tmp_path: Path, profile: ObservatoryProfile, plan: ObservationPlan
) -> None:
    driver = ScriptedDriver()
    runner, spool = _runner(tmp_path, profile, driver)
    spool.save_plan(plan)
    for item_id in ("000", "001"):
        spool.record_outcome(
            ItemOutcome(
                plan_id="plan1",
                item_id=item_id,
                status="observed",
                reason=OutcomeReason.ok,
                started_at=T0,
                finished_at=T0,
            )
        )
    outcomes = await runner.run(plan)
    assert driver.observed == ["002"]
    assert len(outcomes) == 3


async def test_crash_mid_frame(
    tmp_path: Path, profile: ObservatoryProfile, plan: ObservationPlan
) -> None:
    driver = ScriptedDriver()
    runner, spool = _runner(tmp_path, profile, driver)
    spool.save_plan(plan)
    spool.mark_started("plan1", "001")
    orphan = uuid4()
    spool.new_frame_path(orphan).write_bytes(b"partial")
    outcomes = await runner.run(plan)
    assert _reasons(outcomes)[1] == ("failed", "incomplete")
    assert "001" not in driver.observed
    assert (spool.bad_dir / f"{orphan}.fits").exists()


async def test_crash_after_commit(
    tmp_path: Path, profile: ObservatoryProfile, plan: ObservationPlan
) -> None:
    driver = ScriptedDriver()
    runner, spool = _runner(tmp_path, profile, driver)
    spool.save_plan(plan)
    spool.mark_started("plan1", "000")
    frame_id = uuid4()
    path = spool.new_frame_path(frame_id)
    path.write_bytes(b"SIMPLE  =                    T")
    spool.commit_frame(
        FrameRecord(
            frame_id=frame_id,
            plan_id="plan1",
            item_id="000",
            observatory_id="obs1",
            dia_object_id=100,
            alert_id=None,
            obs_start=T0,
            exposure_s=10,
            filter="r",
            driver="simulated",
            driver_version="1",
            ra=30,
            dec=10,
            size_bytes=path.stat().st_size,
            checksums=compute_checksums(path),
        )
    )
    outcomes = await runner.run(plan)
    assert _reasons(outcomes)[0] == ("observed", "ok")
    assert "000" not in driver.observed
