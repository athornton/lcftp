"""Database-backed observation plans and target assignments."""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lcftp_models.plan import AvailabilityWindow, ObservationPlan, PlanStatus

from ..db.schema import Assignment, PlanRow
from ..planner.night import observing_night

__all__ = ["SqlPlanStore"]


class SqlPlanStore:
    """Persistence for issued plans."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, plan: ObservationPlan, timezone: str) -> None:
        """Store a plan and assign each target to the night it is observed.

        The night of each assignment is the observing night (see
        `~lcftp.planner.night.observing_night`) of the item's start time.
        """
        self._session.add(
            PlanRow(
                plan_id=plan.plan_id,
                observatory_id=plan.observatory_id,
                window_start=plan.window.start,
                window_end=plan.window.end,
                created_at=plan.created_at,
                document=plan.model_dump(mode="json"),
                superseded_by=None,
            )
        )
        await self._session.flush()
        nights = {
            i.candidate.dia_object_id: observing_night(i.not_before, timezone)
            for i in plan.items
        }
        for dia_object_id, night in nights.items():
            self._session.add(
                Assignment(
                    plan_id=plan.plan_id,
                    dia_object_id=dia_object_id,
                    observatory_id=plan.observatory_id,
                    night=night,
                )
            )
        await self._session.flush()

    async def get(self, plan_id: str) -> PlanStatus | None:
        """Return a plan and whether it has been superseded."""
        row = await self._session.get(PlanRow, plan_id)
        if row is None:
            return None
        return PlanStatus(
            plan=ObservationPlan.model_validate(row.document),
            superseded_by=row.superseded_by,
        )

    async def supersede_overlapping(
        self, observatory_id: str, window: AvailabilityWindow, new_plan_id: str
    ) -> list[str]:
        """Mark current plans overlapping ``window`` as superseded."""
        stmt = (
            update(PlanRow)
            .where(
                PlanRow.observatory_id == observatory_id,
                PlanRow.superseded_by.is_(None),
                PlanRow.window_start < window.end,
                PlanRow.window_end > window.start,
            )
            .values(superseded_by=new_plan_id)
            .returning(PlanRow.plan_id)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def assignment_counts(
        self, nights: Sequence[date], dia_object_ids: Sequence[int]
    ) -> dict[int, int]:
        """Count observatories assigned each target.

        Counts are per night; for each target the largest count over
        ``nights`` is returned.
        """
        if not dia_object_ids or not nights:
            return {}
        stmt = (
            select(
                Assignment.night,
                Assignment.dia_object_id,
                func.count(func.distinct(Assignment.observatory_id)),
            )
            .join(PlanRow, PlanRow.plan_id == Assignment.plan_id)
            .where(
                Assignment.night.in_(nights),
                Assignment.dia_object_id.in_(dia_object_ids),
                PlanRow.superseded_by.is_(None),
            )
            .group_by(Assignment.night, Assignment.dia_object_id)
        )
        counts: dict[int, int] = {}
        for _, oid, count in (await self._session.execute(stmt)).all():
            counts[int(oid)] = max(counts.get(int(oid), 0), int(count))
        return counts
