"""Availability windows and observation plans."""

from datetime import timedelta
from typing import Self

from pydantic import model_validator

from ._base import ContractModel, UtcDatetime

__all__ = ["MAX_WINDOW", "AvailabilityWindow"]

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
