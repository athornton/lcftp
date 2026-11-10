import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from lcftp_client.spool import Spool
from lcftp_models.checksums import compute_checksums
from lcftp_models.plan import ObservationPlan
from lcftp_models.upload import FrameRecord, ItemOutcome, OutcomeReason

T0 = datetime(2026, 11, 10, 3, tzinfo=UTC)


def _outcome(item_id: str, plan_id: str = "plan1") -> ItemOutcome:
    return ItemOutcome(
        plan_id=plan_id,
        item_id=item_id,
        status="observed",
        reason=OutcomeReason.ok,
        started_at=T0,
        finished_at=T0,
    )


def _frame(spool: Spool, item_id: str = "000") -> FrameRecord:
    frame_id = uuid4()
    path = spool.new_frame_path(frame_id)
    path.write_bytes(b"SIMPLE  =                    T")
    record = FrameRecord(
        frame_id=frame_id,
        plan_id="plan1",
        item_id=item_id,
        observatory_id="obs1",
        dia_object_id=100,
        alert_id=None,
        obs_start=T0,
        exposure_s=10,
        filter="r",
        driver="simulated",
        driver_version="1",
        ra=30,
        dec=10,
        size_bytes=path.stat().st_size,
        checksums=compute_checksums(path),
    )
    spool.commit_frame(record)
    return record


def test_plan_and_outcomes_persist(
    tmp_path: Path, plan: ObservationPlan
) -> None:
    spool = Spool(tmp_path)
    spool.save_plan(plan)
    spool.record_outcome(_outcome("000"))
    again = Spool(tmp_path)
    assert again.current_plan() == plan
    assert list(again.outcomes()) == ["000"]
    assert again.unreported_outcomes() == [_outcome("000")]


def test_outcomes_of_replaced_plan_are_kept(
    tmp_path: Path,
    plan: ObservationPlan,
    plan_factory: Callable[..., ObservationPlan],
) -> None:
    spool = Spool(tmp_path)
    spool.save_plan(plan)
    spool.record_outcome(_outcome("000"))
    spool.save_plan(plan_factory(plan_id="plan2"))
    assert spool.outcomes() == {}
    assert spool.unreported_outcomes() == [_outcome("000")]
    spool.mark_outcomes_reported(spool.unreported_outcomes())
    assert spool.unreported_outcomes() == []


def test_failed_replace_keeps_previous_state(
    tmp_path: Path, plan: ObservationPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = Spool(tmp_path)
    spool.save_plan(plan)
    spool.record_outcome(_outcome("000"))

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        spool.record_outcome(_outcome("001"))
    monkeypatch.undo()
    assert list(Spool(tmp_path).outcomes()) == ["000"]


def test_frames(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    record = _frame(spool)
    assert spool.pending_frames() == [record]
    spool.remove_frame(record.frame_id)
    assert spool.pending_frames() == []
    assert list((tmp_path / "frames").iterdir()) == []


def test_recover_orphans(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    good = _frame(spool)
    orphan = uuid4()
    spool.new_frame_path(orphan).write_bytes(b"partial")
    truncated = uuid4()
    spool.new_frame_path(truncated).write_bytes(b"partial")
    (tmp_path / "frames" / f"{truncated}.json").write_text('{"frame_id": ')

    assert spool.pending_frames() == [good]
    recovered = spool.recover()
    assert sorted(recovered) == sorted([orphan, truncated])
    assert spool.pending_frames() == [good]
    assert (tmp_path / "bad" / f"{orphan}.fits").exists()
    assert (tmp_path / "bad" / f"{truncated}.fits").exists()
    assert spool.bad_count() == 2


def test_mark_bad(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    record = _frame(spool)
    spool.mark_bad(record.frame_id, "checksum mismatch")
    assert spool.pending_frames() == []
    assert (tmp_path / "bad" / f"{record.frame_id}.fits").exists()
    assert (tmp_path / "bad" / f"{record.frame_id}.json").exists()
    assert (
        "checksum mismatch"
        in (tmp_path / "bad" / f"{record.frame_id}.reason").read_text()
    )


def test_items_with_frames(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    _frame(spool, "001")
    assert spool.items_with_frames("plan1") == {"001"}


def test_stop_flag(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    assert not spool.stop_requested()
    spool.request_stop()
    assert Spool(tmp_path).stop_requested()
    spool.clear_stop()
    assert not spool.stop_requested()
