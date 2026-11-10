"""Base class and shared types for contract models."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict

__all__ = ["ContractModel", "UtcDatetime"]


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


type UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
"""A timezone-aware datetime, normalised to UTC. Naive values are rejected."""


class ContractModel(BaseModel):
    """Base for all contract models: immutable, no unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")
