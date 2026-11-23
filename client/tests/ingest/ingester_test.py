import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from support_fits import write_fits

from lcftp_client.ingest.ingester import FrameStatus, ingest_dir
from lcftp_client.spool import Spool
from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.plan import ObservationPlan

PLAN_ID = "aaaaaaaa" + "0" * 24
TAG1 = "lcftp-aaaaaaaa-001"
TAG2 = "lcftp-aaaaaaaa-002"
IN_001 = "2026-11-10T03:12:00"
IN_002 = "2026-11-10T03:22:00"


@pytest.fixture
def spool(
    tmp_path: Path,
    profile: ObservatoryProfile,
    plan_factory: Callable[..., ObservationPlan],
) -> Spool:
    spool = Spool(tmp_path / "spool")
    spool.save_profile(profile)
    spool.save_plan(plan_factory(plan_id=PLAN_ID))
    return spool


def _frame(directory: Path, name: str, **cards: str | float) -> Path:
    defaults: dict[str, str | float] = {
        "DATE_OBS": IN_001,
        "EXPTIME": 60.0,
        "RA": 31.0,
        "DEC": 10.0,
    }
    return write_fits(directory / name, **{**defaults, **cards})


def _statuses(
    spool: Spool, profile: ObservatoryProfile, src: Path, **kwargs: object
) -> list[FrameStatus]:
    report = ingest_dir(spool, profile, src, **kwargs)  # type: ignore[arg-type]
    return [r.status for r in report.results]


def test_ingests_tagged_filename(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    path = _frame(src, f"{TAG1}_LIGHT.fits", TELESCOP="C8")
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert _statuses(spool, profile, src) == [FrameStatus.ingested]
    [record] = spool.pending_frames()
    assert record.plan_id == PLAN_ID
    assert record.item_id == "001"
    assert record.driver == "ingest"
    assert record.driver_metadata["source_name"] == path.name
    assert record.driver_metadata["TELESCOP"] == "C8"
    assert record.ra == 31.0
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert spool.ingested_spans(PLAN_ID)["001"][0] == record.obs_start


def test_tag_from_object_header(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    _frame(src, "frame_0001.fits", OBJECT=TAG1)
    assert _statuses(spool, profile, src) == [FrameStatus.ingested]


def test_conflicting_tags(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    _frame(src, f"{TAG1}.fits", OBJECT=TAG2)
    assert _statuses(spool, profile, src) == [FrameStatus.conflicting_tags]
    assert spool.pending_frames() == []


def test_duplicate_copy_skipped(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    path = _frame(src / "a", f"{TAG1}.fits")
    (src / "b").mkdir()
    (src / "b" / f"{TAG1}_copy.FIT").write_bytes(path.read_bytes())
    assert _statuses(spool, profile, src) == [
        FrameStatus.ingested,
        FrameStatus.already_ingested,
    ]
    assert _statuses(spool, profile, src) == [FrameStatus.already_ingested] * 2
    assert len(spool.pending_frames()) == 1


def test_no_tag_and_item_override(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    _frame(src, "M42.fits")
    assert _statuses(spool, profile, src) == [FrameStatus.no_tag]
    assert _statuses(spool, profile, src, item_tag=TAG1) == [
        FrameStatus.ingested
    ]


@pytest.mark.parametrize(
    ("name", "status"),
    [
        ("lcftp-cccccccc-001.fits", FrameStatus.unknown_plan),
        ("lcftp-aaaaaaaa-099.fits", FrameStatus.unknown_item),
    ],
)
def test_unknown_plan_and_item(
    spool: Spool,
    profile: ObservatoryProfile,
    tmp_path: Path,
    name: str,
    status: FrameStatus,
) -> None:
    src = tmp_path / "src"
    _frame(src, name)
    assert _statuses(spool, profile, src) == [status]


def test_wrong_observatory(
    spool: Spool,
    profile: ObservatoryProfile,
    tmp_path: Path,
    plan_factory: Callable[..., ObservationPlan],
) -> None:
    other = plan_factory(plan_id="bbbbbbbb" + "0" * 24)
    spool.archive_plan(other.model_copy(update={"observatory_id": "other"}))
    src = tmp_path / "src"
    _frame(src, "lcftp-bbbbbbbb-001.fits")
    assert _statuses(spool, profile, src) == [FrameStatus.wrong_observatory]


def test_already_lcftp_and_bad_headers(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    _frame(src, f"a_{TAG1}.fits", LCFTPPLN=PLAN_ID)
    _frame(src, f"b_{TAG1}.fits", EXPTIME=0)
    (src / "c.fits").write_text("not fits")
    assert _statuses(spool, profile, src) == [
        FrameStatus.already_lcftp,
        FrameStatus.bad_headers,
        FrameStatus.bad_headers,
    ]


def test_outside_window_and_force(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    _frame(src, f"{TAG1}.fits", DATE_OBS="2026-11-11T03:12:00")
    assert _statuses(spool, profile, src) == [FrameStatus.outside_window]
    assert _statuses(spool, profile, src, force=True) == [FrameStatus.ingested]


def test_off_target(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    _frame(src, f"{TAG1}.fits", RA=36.0)
    assert _statuses(spool, profile, src) == [FrameStatus.off_target]


def test_late_start_warns(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    _frame(src, f"{TAG1}.fits", DATE_OBS=IN_002)
    [result] = ingest_dir(spool, profile, src).results
    assert result.status == FrameStatus.ingested
    assert result.warning
    assert result.tag == TAG1


def test_dry_run_writes_nothing(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    _frame(src, f"{TAG1}.fits")
    assert _statuses(spool, profile, src, dry_run=True) == [
        FrameStatus.ingested
    ]
    assert spool.pending_frames() == []
    assert spool.ingested_hashes() == set()


def test_ignores_other_files(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    _frame(src, f"{TAG1}.fits")
    (src / f"{TAG1}.xisf").write_text("x")
    report = ingest_dir(spool, profile, src)
    assert report.counts() == {"ingested": 1}


def test_read_only_source(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    path = _frame(src, f"{TAG1}.fits")
    path.chmod(0o444)
    assert _statuses(spool, profile, src) == [FrameStatus.ingested]


def test_copy_failure_is_per_file(
    spool: Spool,
    profile: ObservatoryProfile,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "src"
    _frame(src, f"a_{TAG1}.fits")
    _frame(src, f"b_{TAG2}.fits", DATE_OBS=IN_002, RA=32.0)
    real = shutil.copyfile

    def flaky(source: Path, dest: Path) -> object:
        if source.name.startswith("a_"):
            Path(dest).write_bytes(b"partial")
            raise OSError("disk full")
        return real(source, dest)

    monkeypatch.setattr(shutil, "copyfile", flaky)
    report = ingest_dir(spool, profile, src)
    assert [r.status for r in report.results] == [
        FrameStatus.copy_failed,
        FrameStatus.ingested,
    ]
    assert "disk full" in (report.results[0].detail or "")
    assert len(list(spool.frames_dir.glob("*.fits"))) == 1


def test_reingest_after_lost_record_does_not_duplicate(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    _frame(src, f"{TAG1}.fits")
    ingest_dir(spool, profile, src)
    state_path = spool.root / "state.json"
    state = json.loads(state_path.read_text())
    del state["ingest"]
    state_path.write_text(json.dumps(state))
    ingest_dir(spool, profile, src)
    assert len(spool.pending_frames()) == 1


def test_records_file_name_not_path(
    spool: Spool, profile: ObservatoryProfile, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    _frame(src, f"{TAG1}.fits")
    ingest_dir(spool, profile, src)
    [record] = spool.pending_frames()
    assert record.driver_metadata["source_name"] == f"{TAG1}.fits"
    assert "source_path" not in record.driver_metadata
