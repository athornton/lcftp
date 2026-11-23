"""Record outcomes for a plan once the observer has ingested everything.

The server keeps the first outcome it receives for each item, so ingest
records outcomes only when the observer says they are done.
"""

from lcftp_models.plan import ObservationPlan
from lcftp_models.upload import ItemOutcome, OutcomeReason

from ..spool import Spool

__all__ = ["NO_FRAMES_DETAIL", "finalise_plan"]

NO_FRAMES_DETAIL = "no frames ingested"


def finalise_plan(spool: Spool, plan: ObservationPlan) -> list[ItemOutcome]:
    """Record an outcome for every item of ``plan`` that has none.

    Items with ingested frames are observed, spanning their earliest to
    latest frame; the rest are skipped. Existing outcomes (from a driver
    run, say) are left alone. Returns the outcomes recorded.
    """
    existing = spool.outcomes_for(plan.plan_id)
    spans = spool.ingested_spans(plan.plan_id)
    recorded = []
    for item in plan.items:
        if item.item_id in existing:
            continue
        if item.item_id in spans:
            start, end = spans[item.item_id]
            outcome = ItemOutcome(
                plan_id=plan.plan_id,
                item_id=item.item_id,
                status="observed",
                reason=OutcomeReason.ok,
                started_at=start,
                finished_at=end,
            )
        else:
            outcome = ItemOutcome(
                plan_id=plan.plan_id,
                item_id=item.item_id,
                status="skipped",
                reason=OutcomeReason.other,
                detail=NO_FRAMES_DETAIL,
                started_at=None,
                finished_at=None,
            )
        spool.record_outcome(outcome)
        recorded.append(outcome)
    spool.mark_finalised(plan.plan_id)
    return recorded
