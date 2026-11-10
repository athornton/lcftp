"""Availability-to-plan endpoints for observatory clients."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter

from lcftp_models.errors import ErrorCode, LcftpError
from lcftp_models.plan import AvailabilityWindow, ObservationPlan, PlanStatus

from ..planner.night import observing_night
from ..planner.planner import plan_observations
from ..planner.settings import PlannerSettings
from ..stores.candidates import SqlCandidateStore
from ..stores.observatories import SqlObservatoryStore
from ..stores.plans import SqlPlanStore
from .deps import DeviceDep, SessionDep, SettingsDep

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["plans"])


@router.post("/observatories/{observatory_id}/availability", status_code=201)
async def post_availability(
    observatory_id: str,
    window: AvailabilityWindow,
    device: DeviceDep,
    session: SessionDep,
    settings: SettingsDep,
) -> ObservationPlan:
    """Announce availability and receive an observation plan."""
    if device.observatory_id != observatory_id:
        msg = "This token belongs to a different observatory"
        raise LcftpError(ErrorCode.forbidden, msg, 403)
    profile = await SqlObservatoryStore(session).get(observatory_id)
    if profile is None:  # pragma: no cover (tokens cascade-delete)
        msg = f"Observatory {observatory_id} not found"
        raise LcftpError(ErrorCode.not_found, msg, 404)

    planner = PlannerSettings(observatory_cap=settings.target_observatory_cap)
    plans = SqlPlanStore(session)
    candidates_store = SqlCandidateStore(session)
    plan_id = uuid4().hex
    await plans.supersede_overlapping(observatory_id, window, plan_id)
    candidates = await candidates_store.query(profile.limiting_mag)
    tz = profile.site.timezone
    nights = sorted(
        {observing_night(window.start, tz), observing_night(window.end, tz)}
    )
    counts = await plans.assignment_counts(
        nights, [c.dia_object_id for c in candidates]
    )
    plan = ObservationPlan(
        plan_id=plan_id,
        observatory_id=observatory_id,
        window=window,
        created_at=datetime.now(tz=UTC),
        candidates_as_of=await candidates_store.candidates_as_of(
            settings.brokers
        ),
        planner_version=planner.version,
        items=await asyncio.to_thread(
            plan_observations, profile, window, candidates, counts, planner
        ),
    )
    await plans.save(plan, tz)
    return plan


@router.get("/plans/{plan_id}")
async def get_plan(
    plan_id: str, device: DeviceDep, session: SessionDep
) -> PlanStatus:
    """Return one of this observatory's plans and its status."""
    status = await SqlPlanStore(session).get(plan_id)
    if status is None or status.plan.observatory_id != device.observatory_id:
        msg = f"Plan {plan_id} not found"
        raise LcftpError(ErrorCode.not_found, msg, 404)
    return status
