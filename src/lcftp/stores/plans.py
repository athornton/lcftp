"""Database-backed observation plans and target assignments."""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lcftp_models.plan import AvailabilityWindow, ObservationPlan, PlanStatus

from ..db.schema import Assignment, PlanRow

__all__ = ["SqlPlanStore"]


class SqlPlanStore:
    """Persistence for issued plans."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, plan: ObservationPlan, night: date) -> None:
        """Store a plan and record its targets as assigned for ``night``."""
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
        for dia_object_id in {i.candidate.dia_object_id for i in plan.items}:
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
        self, night: date, dia_object_ids: Sequence[int]
    ) -> dict[int, int]:
        """Count observatories assigned each target on ``night``."""
        if not dia_object_ids:
            return {}
        stmt = (
            select(
                Assignment.dia_object_id,
                func.count(func.distinct(Assignment.observatory_id)),
            )
            .join(PlanRow, PlanRow.plan_id == Assignment.plan_id)
            .where(
                Assignment.night == night,
                Assignment.dia_object_id.in_(dia_object_ids),
                PlanRow.superseded_by.is_(None),
            )
            .group_by(Assignment.dia_object_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {int(oid): int(count) for oid, count in rows}
