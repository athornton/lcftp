"""High-level client used by the CLI and notebooks."""

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import datetime
from types import TracebackType
from typing import Self

import httpx
from pydantic import BaseModel

from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.plan import AvailabilityWindow, ObservationPlan
from lcftp_models.upload import ItemOutcome

from .api import ServerClient
from .clock import Clock, SystemClock
from .config import ClientConfig
from .drivers.base import TelescopeDriver
from .drivers.loader import load_driver
from .runner import Runner
from .spool import Spool
from .uploader import Uploader, UploadSummary

__all__ = ["ClientStatus", "LcftpClient"]


class ClientStatus(BaseModel):
    """Summary of the client's local state."""

    plan_id: str | None
    outcomes: dict[str, int]
    """Count of current-plan outcomes by status."""

    pending_frames: int
    bad_frames: int


class LcftpClient:
    """Plan, observe and upload for one observatory.

    Use as an async context manager::

        async with LcftpClient(load_config()) as client:
            await client.night_once(start, end)
    """

    def __init__(
        self,
        config: ClientConfig,
        *,
        clock: Clock | None = None,
        http: httpx.AsyncClient | None = None,
        driver: TelescopeDriver | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self.spool = Spool(config.spool_dir)
        self._http = http
        self._driver = driver
        self._sleep = sleep
        self.server = ServerClient(
            str(config.server_url), config.token.get_secret_value(), http=http
        )
        self._runner: Runner | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.server.aclose()

    async def profile(self) -> ObservatoryProfile:
        """Return the observatory profile, from the server if reachable."""
        try:
            profile = await self.server.whoami()
        except httpx.TransportError:
            cached = self.spool.profile()
            if cached is None:
                raise
            return cached
        self.spool.save_profile(profile)
        return profile

    async def request_plan(
        self, start: datetime, end: datetime
    ) -> ObservationPlan:
        """Announce availability and store the plan for offline use."""
        await self.profile()
        window = AvailabilityWindow(start=start, end=end)
        plan = await self.server.request_plan(
            self.config.observatory_id, window
        )
        self.spool.save_plan(plan)
        return plan

    async def run(
        self, plan: ObservationPlan | None = None
    ) -> list[ItemOutcome]:
        """Execute a plan (by default the stored one) without the network."""
        plan = plan or self.spool.current_plan()
        if plan is None:
            msg = "There is no plan to run"
            raise RuntimeError(msg)
        profile = self.spool.profile() or await self.profile()
        driver = self._driver or load_driver(
            self.config.driver, self.config.driver_config
        )
        self._runner = Runner(
            driver,
            self.spool,
            self.clock,
            observatory=profile,
            item_timeout_s=self.config.item_timeout_s,
        )
        try:
            return await self._runner.run(plan)
        finally:
            self._runner = None
            self.spool.clear_stop()

    async def upload(self) -> UploadSummary:
        """Upload pending frames and outcomes."""
        uploader = Uploader(
            self.server, self.spool, http=self._http, sleep=self._sleep
        )
        return await uploader.flush()

    def stop(self) -> None:
        """Stop the running (or next) plan; the telescope is parked."""
        if self._runner is not None:
            self._runner.request_stop()
        self.spool.request_stop()

    def status(self) -> ClientStatus:
        """Summarise local state."""
        plan = self.spool.current_plan()
        counts = Counter(str(o.status) for o in self.spool.outcomes().values())
        return ClientStatus(
            plan_id=plan.plan_id if plan else None,
            outcomes=dict(counts),
            pending_frames=len(self.spool.pending_frames()),
            bad_frames=self.spool.bad_count(),
        )

    async def night_once(
        self, start: datetime, end: datetime
    ) -> UploadSummary:
        """Plan, observe and upload for one window.

        If the server is unreachable, the stored plan is used when it
        overlaps the window. Frames that cannot be uploaded stay in the
        spool (see ``UploadSummary.remaining``) for the next attempt.
        """
        try:
            plan = await self.request_plan(start, end)
        except httpx.TransportError:
            stored = self.spool.current_plan()
            if (
                stored is None
                or stored.window.end <= start
                or stored.window.start >= end
            ):
                msg = (
                    "Server unreachable and no stored plan overlaps the window"
                )
                raise RuntimeError(msg) from None
            plan = stored
        await self.run(plan)
        return await self.upload()
