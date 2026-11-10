"""Database-backed candidate cache and broker ingest state."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import ColumnElement, case, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lcftp_models.candidate import BrokerRef, Candidate

from ..db.schema import BrokerStateRow, CandidateRow, CandidateSourceRow

__all__ = ["BrokerState", "SqlCandidateStore"]

_PHOTOMETRY = (
    "ra",
    "dec",
    "mag",
    "band",
    "latest_alert_id",
    "latest_alert_time",
    "classification",
    "score",
)


class BrokerState(BaseModel):
    """Ingest progress and backoff for one broker."""

    model_config = ConfigDict(frozen=True)

    broker: str
    high_water_mark: AwareDatetime | None = None
    last_success: AwareDatetime | None = None
    consecutive_failures: int = 0
    next_attempt: AwareDatetime | None = None


class SqlCandidateStore:
    """The candidate cache, keyed by Rubin diaObjectId."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, candidate: Candidate) -> None:
        """Insert or merge a candidate; the newest alert wins."""
        values = candidate.model_dump(exclude={"sources"})
        stmt = insert(CandidateRow).values(**values)
        newer = (
            stmt.excluded.latest_alert_time > CandidateRow.latest_alert_time
        )
        update: dict[str, ColumnElement[object]] = {
            col: case(
                (newer, stmt.excluded[col]),
                else_=CandidateRow.__table__.c[col],
            )
            for col in _PHOTOMETRY
        }
        update["first_seen"] = func.least(
            CandidateRow.first_seen, stmt.excluded.first_seen
        )
        update["last_updated"] = func.greatest(
            CandidateRow.last_updated, stmt.excluded.last_updated
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[CandidateRow.dia_object_id], set_=update
            )
        )
        for source in candidate.sources:
            await self._session.execute(
                insert(CandidateSourceRow)
                .values(
                    broker=source.broker,
                    broker_object_id=source.broker_object_id,
                    dia_object_id=candidate.dia_object_id,
                )
                .on_conflict_do_nothing()
            )

    async def expire(self, older_than: datetime) -> int:
        """Delete candidates not updated since ``older_than``."""
        stmt = delete(CandidateRow).where(
            CandidateRow.last_updated < older_than
        )
        result = await self._session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)

    async def query(self, max_mag: float) -> list[Candidate]:
        """Return candidates at least as bright as ``max_mag``."""
        stmt = (
            select(CandidateRow)
            .where(CandidateRow.mag <= max_mag)
            .order_by(CandidateRow.dia_object_id)
        )
        rows = list((await self._session.execute(stmt)).scalars())
        sources = await self._sources([r.dia_object_id for r in rows])
        return [
            Candidate(
                dia_object_id=r.dia_object_id,
                sources=sources[r.dia_object_id],
                **{c: getattr(r, c) for c in _PHOTOMETRY},
                first_seen=r.first_seen,
                last_updated=r.last_updated,
            )
            for r in rows
        ]

    async def candidates_as_of(
        self, brokers: Sequence[str]
    ) -> datetime | None:
        """Return the oldest last successful poll among ``brokers``."""
        stmt = select(func.min(BrokerStateRow.last_success)).where(
            BrokerStateRow.broker.in_(brokers)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_state(self, broker: str) -> BrokerState:
        """Return a broker's ingest state (defaults if never polled)."""
        row = await self._session.get(BrokerStateRow, broker)
        if row is None:
            return BrokerState(broker=broker)
        return BrokerState(
            broker=row.broker,
            high_water_mark=row.high_water_mark,
            last_success=row.last_success,
            consecutive_failures=row.consecutive_failures,
            next_attempt=row.next_attempt,
        )

    async def save_state(self, state: BrokerState) -> None:
        """Persist a broker's ingest state."""
        values = state.model_dump()
        stmt = insert(BrokerStateRow).values(**values)
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[BrokerStateRow.broker],
                set_={k: v for k, v in values.items() if k != "broker"},
            )
        )

    async def _sources(self, ids: Sequence[int]) -> dict[int, list[BrokerRef]]:
        result: dict[int, list[BrokerRef]] = defaultdict(list)
        if not ids:
            return result
        stmt = (
            select(CandidateSourceRow)
            .where(CandidateSourceRow.dia_object_id.in_(ids))
            .order_by(CandidateSourceRow.broker)
        )
        for row in (await self._session.execute(stmt)).scalars():
            result[row.dia_object_id].append(
                BrokerRef(
                    broker=row.broker, broker_object_id=row.broker_object_id
                )
            )
        return result
