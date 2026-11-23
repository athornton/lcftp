from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from lcftp_client.ingest.finalise import finalise_plan
from lcftp_client.spool import Spool
from lcftp_models.plan import ObservationPlan
from lcftp_models.upload import ItemOutcome, OutcomeReason


def test_finalise_records_observed_and_skipped(
    tmp_path: Path, plan_factory: Callable[..., ObservationPlan]
) -> None:
    spool = Spool(tmp_path)
    plan = plan_factory(plan_id="p")
    spool.save_plan(plan)
    start = plan.items[1].not_before
    end = start + timedelta(minutes=3)
    spool.record_ingested("h", "p", "001", start, end)

    recorded = finalise_plan(spool, plan)

    outcomes = spool.outcomes_for("p")
    assert {o.item_id for o in recorded} == {"000", "001", "002"}
    assert outcomes["001"].status == "observed"
    assert outcomes["001"].reason == OutcomeReason.ok
    assert outcomes["001"].started_at == start
    assert outcomes["001"].finished_at == end
    for item_id in ("000", "002"):
        assert outcomes[item_id].status == "skipped"
        assert outcomes[item_id].reason == OutcomeReason.other
        assert outcomes[item_id].detail == "no frames ingested"
        assert outcomes[item_id].started_at is None
    assert spool.unfinalised_plans() == []
    assert len(spool.unreported_outcomes()) == 3


def test_finalise_keeps_existing_outcome(
    tmp_path: Path, plan_factory: Callable[..., ObservationPlan]
) -> None:
    spool = Spool(tmp_path)
    plan = plan_factory(plan_id="p")
    spool.save_plan(plan)
    existing = ItemOutcome(
        plan_id="p",
        item_id="000",
        status="failed",
        reason=OutcomeReason.driver_error,
        started_at=None,
        finished_at=None,
    )
    spool.record_outcome(existing)
    recorded = finalise_plan(spool, plan)
    assert {o.item_id for o in recorded} == {"001", "002"}
    assert spool.outcomes_for("p")["000"] == existing
