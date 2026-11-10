from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from lcftp_models.candidate import Candidate
from lcftp_models.plan import (
    AvailabilityWindow,
    ExposureHint,
    ObservationPlan,
    PlanItem,
    PlanStatus,
)

T0 = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)


def _item(candidate: Candidate, item_id: str, start: datetime) -> PlanItem:
    return PlanItem(
        item_id=item_id,
        candidate=candidate,
        not_before=start,
        not_after=start + timedelta(minutes=10),
        suggested_filter="r",
        exposure_hint=ExposureHint(total_exposure_s=300),
        priority=0,
    )


def _plan(items: list[PlanItem]) -> ObservationPlan:
    return ObservationPlan(
        plan_id="p1",
        observatory_id="obs1",
        window=AvailabilityWindow(start=T0, end=T0 + timedelta(hours=8)),
        created_at=T0,
        candidates_as_of=None,
        planner_version="1",
        items=items,
    )


def test_exposure_hint_requires_a_value() -> None:
    with pytest.raises(ValidationError):
        ExposureHint()


def test_item_requires_positive_duration(candidate: Candidate) -> None:
    with pytest.raises(ValidationError):
        PlanItem(
            item_id="000",
            candidate=candidate,
            not_before=T0,
            not_after=T0,
            suggested_filter=None,
            exposure_hint=ExposureHint(target_snr=10),
            priority=0,
        )


def test_empty_plan_is_valid() -> None:
    assert _plan([]).items == []


def test_plan_rejects_overlapping_items(candidate: Candidate) -> None:
    with pytest.raises(ValidationError):
        _plan(
            [
                _item(candidate, "000", T0),
                _item(candidate, "001", T0 + timedelta(minutes=5)),
            ]
        )


def test_plan_rejects_unsorted_items(candidate: Candidate) -> None:
    with pytest.raises(ValidationError):
        _plan(
            [
                _item(candidate, "000", T0 + timedelta(minutes=20)),
                _item(candidate, "001", T0),
            ]
        )


def test_plan_allows_adjacent_items(candidate: Candidate) -> None:
    plan = _plan(
        [
            _item(candidate, "000", T0),
            _item(candidate, "001", T0 + timedelta(minutes=10)),
        ]
    )
    assert [i.item_id for i in plan.items] == ["000", "001"]


def test_plan_rejects_duplicate_item_ids(candidate: Candidate) -> None:
    with pytest.raises(ValidationError):
        _plan(
            [
                _item(candidate, "000", T0),
                _item(candidate, "000", T0 + timedelta(minutes=20)),
            ]
        )


def test_plan_status_round_trip() -> None:
    status = PlanStatus(plan=_plan([]), superseded_by="p2")
    assert PlanStatus.model_validate_json(status.model_dump_json()) == status
