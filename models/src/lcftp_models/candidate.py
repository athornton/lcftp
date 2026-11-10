"""Broker-normalised candidate targets."""

from typing import Annotated

from pydantic import Field

from ._base import ContractModel, UtcDatetime

__all__ = ["BrokerRef", "Candidate"]


class BrokerRef(ContractModel):
    """Identity of an object at a particular broker."""

    broker: Annotated[str, Field(min_length=1)]
    broker_object_id: Annotated[str, Field(min_length=1)]


class Candidate(ContractModel):
    """A potential follow-up target, normalised across brokers."""

    dia_object_id: int
    """Rubin ``diaObjectId``, the cross-broker identity."""

    sources: Annotated[list[BrokerRef], Field(min_length=1)]
    latest_alert_id: str | None
    latest_alert_time: UtcDatetime
    ra: Annotated[float, Field(ge=0, lt=360)]
    dec: Annotated[float, Field(ge=-90, le=90)]
    mag: float
    """AB magnitude of the latest detection."""

    band: str
    classification: str | None = None
    score: float | None = None
    first_seen: UtcDatetime
    last_updated: UtcDatetime
