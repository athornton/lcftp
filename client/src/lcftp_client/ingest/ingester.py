"""Match observer frames to plan items and commit them to the spool.

Ingest never guesses: a frame is matched only by a target tag in its
file name or ``OBJECT`` header, or by a tag the observer names
explicitly. Source files are copied, never moved or changed.
"""

import hashlib
import shutil
from collections import Counter
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

import astropy.units as u
from astropy.coordinates import SkyCoord
from pydantic import BaseModel, ConfigDict

from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.plan import ObservationPlan, PlanItem

from .. import __version__
from ..provenance import commit_frame
from ..spool import Spool
from ..tags import TagRef, find_tags, target_tag
from .headers import FrameInfo, HeaderError, read_frame_info

__all__ = [
    "FITS_SUFFIXES",
    "INGEST_DRIVER",
    "FrameResult",
    "FrameStatus",
    "IngestReport",
    "ingest_dir",
]

FITS_SUFFIXES = frozenset({".fits", ".fit", ".fts"})
INGEST_DRIVER = "ingest"


class FrameStatus(StrEnum):
    """What ingest did with one file."""

    ingested = "ingested"
    already_ingested = "already_ingested"
    already_lcftp = "already_lcftp"
    no_tag = "no_tag"
    conflicting_tags = "conflicting_tags"
    unknown_plan = "unknown_plan"
    unknown_item = "unknown_item"
    wrong_observatory = "wrong_observatory"
    bad_headers = "bad_headers"
    outside_window = "outside_window"
    off_target = "off_target"


class FrameResult(BaseModel):
    """The outcome of ingesting one file."""

    model_config = ConfigDict(frozen=True)

    path: Path
    status: FrameStatus
    tag: str | None = None
    detail: str | None = None
    warning: str | None = None


class IngestReport(BaseModel):
    """The outcome of ingesting a directory."""

    results: list[FrameResult]

    def counts(self) -> dict[str, int]:
        """Return the number of files with each status."""
        return dict(Counter(str(r.status) for r in self.results))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frames(directory: Path) -> list[Path]:
    return sorted(
        p
        for p in directory.rglob("*")
        if p.is_file() and p.suffix.lower() in FITS_SUFFIXES
    )


def _resolve_tag(
    path: Path, info: FrameInfo, item_tag: str | None
) -> TagRef | FrameResult:
    tags = find_tags(path.name)
    for tag in find_tags(info.object or ""):
        if tag not in tags:
            tags.append(tag)
    if len(tags) > 1:
        names = ", ".join(target_tag(t.plan8, t.item_id) for t in tags)
        return FrameResult(
            path=path, status=FrameStatus.conflicting_tags, detail=names
        )
    if tags:
        return tags[0]
    override = find_tags(item_tag or "")
    if override:
        return override[0]
    return FrameResult(path=path, status=FrameStatus.no_tag)


def _sanity_problem(
    plan: ObservationPlan,
    item: PlanItem,
    info: FrameInfo,
    profile: ObservatoryProfile,
) -> tuple[FrameStatus, str] | None:
    """Catch tag typos and reused tags: wrong night, or wrong target."""
    start = info.obs_start
    if not plan.window.start <= start <= plan.window.end:
        return FrameStatus.outside_window, (
            f"DATE-OBS {start.isoformat()} is outside the plan's window "
            f"{plan.window.start.isoformat()} to "
            f"{plan.window.end.isoformat()}"
        )
    if info.ra is None or info.dec is None:
        return None
    target = SkyCoord(item.candidate.ra * u.deg, item.candidate.dec * u.deg)
    pointing = SkyCoord(info.ra * u.deg, info.dec * u.deg)
    offset = target.separation(pointing).arcmin
    if offset > profile.fov_arcmin:
        return FrameStatus.off_target, (
            f"pointing is {offset:.0f}' from the target; "
            f"field of view is {profile.fov_arcmin:g}'"
        )
    return None


class _Rejection(NamedTuple):
    """Why a frame cannot be ingested."""

    status: FrameStatus
    detail: str


class _Accepted(NamedTuple):
    """A frame that passed every check, ready to commit."""

    sha256: str
    info: FrameInfo
    plan: ObservationPlan
    item: PlanItem


def _copy_and_commit(
    spool: Spool, profile: ObservatoryProfile, path: Path, frame: _Accepted
) -> None:
    info, plan, item = frame.info, frame.plan, frame.item
    frame_id = uuid4()
    dest = spool.new_frame_path(frame_id)
    shutil.copy2(path, dest)
    commit_frame(
        spool,
        dest,
        frame_id=frame_id,
        plan=plan,
        item=item,
        observatory_id=profile.id,
        driver=INGEST_DRIVER,
        driver_version=__version__,
        obs_start=info.obs_start,
        exposure_s=info.exposure_s,
        filter=info.filter,
        ra=item.candidate.ra if info.ra is None else info.ra,
        dec=item.candidate.dec if info.dec is None else info.dec,
        driver_metadata={
            **info.metadata,
            "source_path": str(path),
            "source_sha256": frame.sha256,
        },
    )
    spool.record_ingested(
        frame.sha256,
        plan.plan_id,
        item.item_id,
        info.obs_start,
        info.obs_start + timedelta(seconds=info.exposure_s),
    )


def _read(
    path: Path, seen: set[str], item_tag: str | None
) -> tuple[str, FrameInfo, TagRef] | FrameResult:
    """Hash and read a file, and find its tag."""
    sha256 = _sha256(path)
    if sha256 in seen:
        return FrameResult(path=path, status=FrameStatus.already_ingested)
    try:
        info = read_frame_info(path)
    except HeaderError as e:
        return FrameResult(
            path=path, status=FrameStatus.bad_headers, detail=str(e)
        )
    if info.lcftp_stamped:
        return FrameResult(path=path, status=FrameStatus.already_lcftp)
    ref = _resolve_tag(path, info, item_tag)
    if isinstance(ref, FrameResult):
        return ref
    return sha256, info, ref


def _match(
    spool: Spool, profile: ObservatoryProfile, ref: TagRef
) -> tuple[ObservationPlan, PlanItem] | _Rejection:
    """Find the plan item a tag names, for this observatory."""
    try:
        plan = spool.find_plan(ref.plan8)
    except LookupError as e:
        return _Rejection(FrameStatus.unknown_plan, str(e))
    items = {i.item_id: i for i in plan.items}
    if ref.item_id not in items:
        return _Rejection(
            FrameStatus.unknown_item,
            f"plan {plan.plan_id} has no item {ref.item_id}",
        )
    if plan.observatory_id != profile.id:
        return _Rejection(
            FrameStatus.wrong_observatory,
            f"plan is for observatory {plan.observatory_id}",
        )
    return plan, items[ref.item_id]


def _ingest_one(
    spool: Spool,
    profile: ObservatoryProfile,
    path: Path,
    seen: set[str],
    *,
    item_tag: str | None,
    force: bool,
    dry_run: bool,
) -> FrameResult:
    read = _read(path, seen, item_tag)
    if isinstance(read, FrameResult):
        return read
    sha256, info, ref = read
    tag = target_tag(ref.plan8, ref.item_id)
    match = _match(spool, profile, ref)
    if isinstance(match, _Rejection):
        return FrameResult(
            path=path, status=match.status, tag=tag, detail=match.detail
        )
    plan, item = match
    if not force and (problem := _sanity_problem(plan, item, info, profile)):
        return FrameResult(
            path=path, status=problem[0], tag=tag, detail=problem[1]
        )
    warning = None
    if not item.not_before <= info.obs_start <= item.not_after:
        warning = "taken outside the target's planned window"
    if not dry_run:
        _copy_and_commit(
            spool, profile, path, _Accepted(sha256, info, plan, item)
        )
    seen.add(sha256)
    return FrameResult(
        path=path, status=FrameStatus.ingested, tag=tag, warning=warning
    )


def ingest_dir(
    spool: Spool,
    profile: ObservatoryProfile,
    directory: Path,
    *,
    item_tag: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> IngestReport:
    """Ingest every FITS file under ``directory``, recursively.

    Parameters
    ----------
    item_tag
        Target tag for frames that carry none of their own.
    force
        Accept frames outside the plan's window or away from the target.
    dry_run
        Report what would happen without copying or recording anything.
    """
    seen = spool.ingested_hashes()
    return IngestReport(
        results=[
            _ingest_one(
                spool,
                profile,
                path,
                seen,
                item_tag=item_tag,
                force=force,
                dry_run=dry_run,
            )
            for path in _frames(directory)
        ]
    )
