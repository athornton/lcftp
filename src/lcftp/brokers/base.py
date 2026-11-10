"""The broker plugin protocol."""

from collections.abc import AsyncIterator, Callable, Mapping
from datetime import datetime
from typing import Protocol

from lcftp_models.candidate import Candidate

__all__ = ["BrokerFactory", "BrokerSource", "BrokerUnavailableError"]


class BrokerUnavailableError(Exception):
    """The broker could not be reached or returned an error."""


class BrokerSource(Protocol):
    """A source of candidates from one alert broker.

    ``poll`` must yield only candidates whose ``mag`` is at most
    ``mag_limit`` and whose ``latest_alert_time`` is after ``since``, and
    every candidate must include a `~lcftp_models.candidate.BrokerRef`
    whose ``broker`` equals ``name``. Transient failures are reported by
    raising `BrokerUnavailableError`.
    """

    @property
    def name(self) -> str:
        """Broker name used in `~lcftp_models.candidate.BrokerRef`."""

    @property
    def version(self) -> str:
        """Plugin version, recorded for provenance."""

    def poll(
        self, since: datetime, mag_limit: float
    ) -> AsyncIterator[Candidate]:
        """Yield candidates updated after ``since`` and brighter than limit."""


type BrokerFactory = Callable[[Mapping[str, str]], BrokerSource]
"""Entry points in ``lcftp.brokers`` must be factories of this type."""
