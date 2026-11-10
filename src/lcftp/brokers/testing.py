"""Test helpers for broker plugins: a fake broker and a contract check."""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

from lcftp_models.candidate import BrokerRef, Candidate

from .base import BrokerSource, BrokerUnavailableError

__all__ = ["FakeBroker", "assert_broker_contract", "make_candidate"]


def make_candidate(
    dia_object_id: int,
    *,
    mag: float = 15.0,
    when: datetime | None = None,
    ra: float = 30.0,
    dec: float = 0.0,
    broker: str = "fake",
    band: str = "r",
) -> Candidate:
    """Build a candidate with sensible defaults."""
    when = when or datetime.now(tz=UTC)
    return Candidate(
        dia_object_id=dia_object_id,
        sources=[
            BrokerRef(broker=broker, broker_object_id=str(dia_object_id))
        ],
        latest_alert_id=f"{dia_object_id}00",
        latest_alert_time=when,
        ra=ra,
        dec=dec,
        mag=mag,
        band=band,
        first_seen=when,
        last_updated=when,
    )


class FakeBroker:
    """A broker that serves a fixed list of candidates."""

    def __init__(
        self,
        candidates: Sequence[Candidate],
        *,
        name: str = "fake",
        fail_times: int = 0,
    ) -> None:
        self._name = name
        self._candidates = [
            c.model_copy(
                update={
                    "sources": [
                        BrokerRef(
                            broker=name, broker_object_id=str(c.dia_object_id)
                        )
                    ]
                }
            )
            for c in candidates
        ]
        self._failures_left = fail_times
        self.polls: list[tuple[datetime, float]] = []

    @property
    def name(self) -> str:
        """Broker name."""
        return self._name

    @property
    def version(self) -> str:
        """Plugin version."""
        return "0"

    async def poll(
        self, since: datetime, mag_limit: float
    ) -> AsyncIterator[Candidate]:
        """Yield the matching fixed candidates."""
        self.polls.append((since, mag_limit))
        if self._failures_left > 0:
            self._failures_left -= 1
            msg = f"{self._name} is unavailable"
            raise BrokerUnavailableError(msg)
        for candidate in self._candidates:
            if (
                candidate.mag <= mag_limit
                and candidate.latest_alert_time > since
            ):
                yield candidate


async def assert_broker_contract(
    broker: BrokerSource, *, since: datetime, mag_limit: float
) -> list[Candidate]:
    """Poll ``broker`` and assert the `BrokerSource` contract holds.

    Returns the candidates so callers can make further assertions.
    """
    assert broker.name, "broker must have a name"
    assert broker.version, "broker must have a version"
    found = [c async for c in broker.poll(since, mag_limit)]
    for candidate in found:
        assert candidate.mag <= mag_limit, candidate
        assert candidate.latest_alert_time > since, candidate
        assert broker.name in {s.broker for s in candidate.sources}, candidate
    return found
