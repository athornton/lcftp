"""The telescope driver plugin protocol."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, JsonValue

from lcftp_models.plan import PlanItem

from ..clock import Clock

__all__ = [
    "CapturedFrame",
    "DriverError",
    "DriverFactory",
    "DriverResult",
    "ObservationContext",
    "TelescopeDriver",
]


class DriverError(Exception):
    """The telescope failed to carry out an observation."""


class CapturedFrame(BaseModel):
    """A frame written by a driver, with what was actually done."""

    model_config = ConfigDict(frozen=True)

    frame_id: UUID
    path: Path
    obs_start: AwareDatetime
    exposure_s: float
    filter: str | None
    ra: float
    dec: float
    driver_metadata: dict[str, JsonValue] = {}


class DriverResult(BaseModel):
    """The result of observing one plan item."""

    model_config = ConfigDict(frozen=True)

    frames: list[CapturedFrame]
    weather_blocked: bool = False


@dataclass(frozen=True)
class ObservationContext:
    """What a driver needs besides the plan item."""

    plan_id: str
    observatory_id: str
    fov_arcmin: float
    new_frame_path: Callable[[UUID], Path]
    """Where to write a frame with a given id; drivers must use this."""

    clock: Clock
    extra: Mapping[str, JsonValue] = field(default_factory=dict)


class TelescopeDriver(Protocol):
    """Turns plan intent into actions on a particular telescope.

    Drivers must write frames only to paths from
    ``ObservationContext.new_frame_path``, raise `DriverError` on
    hardware failure, and report weather with ``weather_blocked``.
    ``park`` must be safe to call at any time and more than once.
    """

    @property
    def name(self) -> str:
        """Entry-point name of the driver."""

    @property
    def version(self) -> str:
        """Driver version, recorded in every frame."""

    async def connect(self) -> None:
        """Connect to the telescope."""

    async def disconnect(self) -> None:
        """Disconnect from the telescope."""

    async def observe(
        self, item: PlanItem, ctx: ObservationContext
    ) -> DriverResult:
        """Observe one plan item."""

    async def park(self) -> None:
        """Park the telescope safely."""


type DriverFactory = Callable[[Mapping[str, JsonValue]], TelescopeDriver]
"""Entry points in ``lcftp.drivers`` must be factories of this type."""
