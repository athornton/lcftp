"""Shared fixtures for lcftp-client tests."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from lcftp_models.candidate import BrokerRef, Candidate
from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.plan import (
    AvailabilityWindow,
    ExposureHint,
    ObservationPlan,
    PlanItem,
)
from lcftp_models.site import FilterSpec, SiteLocation

T0 = datetime(2026, 11, 10, 3, tzinfo=UTC)


def make_plan(
    n_items: int = 3, plan_id: str = "plan1", start: datetime = T0
) -> ObservationPlan:
    """Build a plan with ``n_items`` ten-minute items."""
    items = []
    for i in range(n_items):
        begin = start + timedelta(minutes=10 * i)
        candidate = Candidate(
            dia_object_id=100 + i,
            sources=[BrokerRef(broker="fake", broker_object_id=str(100 + i))],
            latest_alert_id=f"{100 + i}00",
            latest_alert_time=start,
            ra=30.0 + i,
            dec=10.0,
            mag=12.0,
            band="r",
            first_seen=start,
            last_updated=start,
        )
        items.append(
            PlanItem(
                item_id=f"{i:03d}",
                candidate=candidate,
                not_before=begin,
                not_after=begin + timedelta(minutes=10),
                suggested_filter="r",
                exposure_hint=ExposureHint(total_exposure_s=300),
                priority=i,
            )
        )
    return ObservationPlan(
        plan_id=plan_id,
        observatory_id="obs1",
        window=AvailabilityWindow(start=start, end=start + timedelta(hours=8)),
        created_at=start,
        candidates_as_of=start,
        planner_version="1",
        items=items,
    )


@pytest.fixture
def plan_factory() -> Callable[..., ObservationPlan]:
    return make_plan


@pytest.fixture
def plan() -> ObservationPlan:
    return make_plan()


@pytest.fixture
def profile() -> ObservatoryProfile:
    return ObservatoryProfile(
        id="obs1",
        owner="athornton",
        name="Backyard",
        site=SiteLocation(
            latitude=32.23,
            longitude=-110.95,
            elevation_m=728,
            timezone="America/Phoenix",
        ),
        aperture_m=0.05,
        limiting_mag=15,
        fov_arcmin=77,
        filters=[FilterSpec(name="r", wavelength_nm=620)],
    )
