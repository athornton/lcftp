"""Execute an observation plan offline."""

import asyncio
import contextlib
from collections.abc import Awaitable
from datetime import datetime

from lcftp_models.checksums import compute_checksums
from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.plan import ObservationPlan, PlanItem
from lcftp_models.upload import (
    FrameRecord,
    ItemOutcome,
    OutcomeReason,
    OutcomeStatus,
)

from .clock import Clock
from .drivers.base import (
    CapturedFrame,
    DriverError,
    ObservationContext,
    TelescopeDriver,
)
from .fits import stamp_provenance
from .spool import Spool

__all__ = ["STOP_POLL_S", "Runner"]

STOP_POLL_S = 1.0
"""How often a waiting runner checks the spool's STOP file."""


class Runner:
    """Runs a plan item by item, recording every outcome in the spool.

    A failing item never ends the night. The telescope is parked when the
    run ends, however it ends.
    """

    def __init__(
        self,
        driver: TelescopeDriver,
        spool: Spool,
        clock: Clock,
        *,
        observatory: ObservatoryProfile,
        item_timeout_s: float,
    ) -> None:
        self._driver = driver
        self._spool = spool
        self._clock = clock
        self._observatory = observatory
        self._timeout = item_timeout_s
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        """Stop after the current item; remaining items are skipped."""
        self._stop.set()

    def _stopping(self) -> bool:
        return self._stop.is_set() or self._spool.stop_requested()

    async def run(self, plan: ObservationPlan) -> list[ItemOutcome]:
        """Execute ``plan``, resuming where a previous run left off."""
        self._spool.save_plan(plan)
        self._spool.recover()
        started = self._spool.started_items(plan.plan_id)
        with_frames = self._spool.items_with_frames(plan.plan_id)
        await self._driver.connect()
        try:
            for item in plan.items:
                if item.item_id in self._spool.outcomes():
                    continue
                if item.item_id in started:
                    if item.item_id in with_frames:
                        self._record(plan, item, "observed", OutcomeReason.ok)
                    else:
                        self._record(
                            plan, item, "failed", OutcomeReason.incomplete
                        )
                    continue
                await self._run_item(plan, item)
        finally:
            await self._driver.park()
            await self._driver.disconnect()
        outcomes = self._spool.outcomes()
        return [outcomes[i.item_id] for i in plan.items]

    async def _run_item(self, plan: ObservationPlan, item: PlanItem) -> None:
        if self._stopping():
            self._record(plan, item, "skipped", OutcomeReason.stopped)
            return
        if self._clock.now() >= item.not_after:
            self._record(plan, item, "skipped", OutcomeReason.window_passed)
            return
        if not await self._wait_until(item.not_before):
            self._record(plan, item, "skipped", OutcomeReason.stopped)
            return
        started = self._clock.now()
        self._spool.mark_started(plan.plan_id, item.item_id)
        ctx = ObservationContext(
            plan_id=plan.plan_id,
            observatory_id=self._observatory.id,
            fov_arcmin=self._observatory.fov_arcmin,
            new_frame_path=self._spool.new_frame_path,
            clock=self._clock,
        )
        try:
            result = await asyncio.wait_for(
                self._driver.observe(item, ctx), self._timeout
            )
        except TimeoutError:
            self._record(
                plan, item, "failed", OutcomeReason.timeout, started=started
            )
            return
        except DriverError as e:
            self._record(
                plan,
                item,
                "failed",
                OutcomeReason.driver_error,
                detail=str(e),
                started=started,
            )
            return
        if result.weather_blocked:
            self._record(
                plan, item, "skipped", OutcomeReason.weather, started=started
            )
            return
        for frame in result.frames:
            self._commit(plan, item, frame)
        self._record(plan, item, "observed", OutcomeReason.ok, started=started)

    async def _wait_until(self, when: datetime) -> bool:
        """Wait until ``when``; return False if a stop was requested."""
        sleep = asyncio.ensure_future(self._clock.sleep_until(when))
        stop = asyncio.ensure_future(self._watch_stop())
        try:
            await asyncio.wait(
                {sleep, stop}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (sleep, stop):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        return not self._stopping()

    async def _watch_stop(self) -> None:
        while not self._stopping():
            waiter: Awaitable[bool] = self._stop.wait()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(waiter, STOP_POLL_S)

    def _commit(
        self, plan: ObservationPlan, item: PlanItem, frame: CapturedFrame
    ) -> None:
        stamp_provenance(
            frame.path,
            plan_id=plan.plan_id,
            item_id=item.item_id,
            observatory_id=self._observatory.id,
            dia_object_id=item.candidate.dia_object_id,
            alert_id=item.candidate.latest_alert_id,
            driver=self._driver.name,
            driver_version=self._driver.version,
        )
        self._spool.commit_frame(
            FrameRecord(
                frame_id=frame.frame_id,
                plan_id=plan.plan_id,
                item_id=item.item_id,
                observatory_id=self._observatory.id,
                dia_object_id=item.candidate.dia_object_id,
                alert_id=item.candidate.latest_alert_id,
                obs_start=frame.obs_start,
                exposure_s=frame.exposure_s,
                filter=frame.filter,
                driver=self._driver.name,
                driver_version=self._driver.version,
                ra=frame.ra,
                dec=frame.dec,
                size_bytes=frame.path.stat().st_size,
                checksums=compute_checksums(frame.path),
                driver_metadata=frame.driver_metadata,
            )
        )

    def _record(
        self,
        plan: ObservationPlan,
        item: PlanItem,
        status: OutcomeStatus,
        reason: OutcomeReason,
        *,
        detail: str | None = None,
        started: datetime | None = None,
    ) -> None:
        self._spool.record_outcome(
            ItemOutcome(
                plan_id=plan.plan_id,
                item_id=item.item_id,
                status=status,
                reason=reason,
                detail=detail,
                started_at=started,
                finished_at=self._clock.now(),
            )
        )
