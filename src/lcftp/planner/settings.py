"""Planner settings."""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict

__all__ = ["PlannerSettings"]


class PlannerSettings(BaseModel):
    """Tunable parameters of the planner."""

    model_config = ConfigDict(frozen=True)

    grid_step: timedelta = timedelta(minutes=5)
    """Time resolution of visibility calculations."""

    slot: timedelta = timedelta(minutes=10)
    """Telescope time allotted to each target, including slewing."""

    default_exposure_s: float = 300.0
    observatory_cap: int = 3
    """Observatories per target per night before ranking penalises it."""

    version: str = "1"
