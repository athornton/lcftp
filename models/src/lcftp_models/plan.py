"""Availability windows and observation plans."""

from datetime import timedelta
from itertools import pairwise
from typing import Annotated, Self

from pydantic import Field, model_validator

from ._base import ContractModel, UtcDatetime
from .candidate import Candidate

__all__ = [
    "MAX_WINDOW",
    "AvailabilityWindow",
    "ExposureHint",
    "ObservationPlan",
    "PlanItem",
    "PlanStatus",
]

MAX_WINDOW = timedelta(hours=24)
"""Longest availability window that may be requested."""


class AvailabilityWindow(ContractModel):
    """A period during which an observatory may be used."""

    start: UtcDatetime
    end: UtcDatetime

    @model_validator(mode="after")
    def _check_span(self) -> Self:
        if self.end <= self.start:
            msg = "end must be after start"
            raise ValueError(msg)
        if self.end - self.start > MAX_WINDOW:
            msg = f"window may not exceed {MAX_WINDOW}"
            raise ValueError(msg)
        return self


class ExposureHint(ContractModel):
    """Suggested exposure; drivers decide how to achieve it."""

    total_exposure_s: Annotated[float, Field(gt=0)] | None = None
    target_snr: Annotated[float, Field(gt=0)] | None = None

    @model_validator(mode="after")
    def _check_set(self) -> Self:
        if self.total_exposure_s is None and self.target_snr is None:
            msg = "set total_exposure_s or target_snr"
            raise ValueError(msg)
        return self


class PlanItem(ContractModel):
    """One target to observe: intent, not telescope commands."""

    item_id: Annotated[str, Field(min_length=1)]
    candidate: Candidate
    """Snapshot of the candidate as it was when the plan was made."""

    not_before: UtcDatetime
    not_after: UtcDatetime
    suggested_filter: str | None
    exposure_hint: ExposureHint
    priority: Annotated[int, Field(ge=0)]
    """Rank by planner score; 0 is the most desirable."""

    @model_validator(mode="after")
    def _check_times(self) -> Self:
        if self.not_after <= self.not_before:
            msg = "not_after must be after not_before"
            raise ValueError(msg)
        return self


class ObservationPlan(ContractModel):
    """An immutable plan for one observatory and availability window."""

    plan_id: str
    observatory_id: str
    window: AvailabilityWindow
    created_at: UtcDatetime
    candidates_as_of: UtcDatetime | None
    """When the candidate cache was last refreshed from the brokers."""

    planner_version: str
    items: list[PlanItem]

    @model_validator(mode="after")
    def _check_items(self) -> Self:
        if len({i.item_id for i in self.items}) != len(self.items):
            msg = "item_id values must be unique"
            raise ValueError(msg)
        for prev, item in pairwise(self.items):
            if item.not_before < prev.not_after:
                msg = "items must be time-ordered and non-overlapping"
                raise ValueError(msg)
        return self


class PlanStatus(ContractModel):
    """A plan together with whether it has been replaced."""

    plan: ObservationPlan
    superseded_by: str | None
