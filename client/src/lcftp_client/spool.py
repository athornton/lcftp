"""Crash-safe on-disk storage for plans, outcomes and frames.

Layout under the spool root::

    profile.json          the observatory profile, cached for offline use
    plan.json             the current plan
    state.json            item outcomes (all plans) and which were reported
    frames/<id>.fits      captured frames
    frames/<id>.json      their FrameRecord sidecars, written last
    bad/                  frames that cannot be uploaded, with .reason files
    STOP                  present when a stop has been requested

Every JSON file is replaced atomically, so a crash leaves either the old
or the new version.
"""

import contextlib
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.plan import ObservationPlan
from lcftp_models.upload import FrameRecord, ItemOutcome

__all__ = ["Spool"]


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _key(plan_id: str, item_id: str) -> str:
    return f"{plan_id}/{item_id}"


class Spool:
    """The client's durable working directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.frames_dir = root / "frames"
        self.bad_dir = root / "bad"
        for directory in (root, self.frames_dir, self.bad_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self._plan_path = root / "plan.json"
        self._state_path = root / "state.json"
        self._stop_path = root / "STOP"
        self._profile_path = root / "profile.json"

    # Observatory profile (cached for offline use)

    def save_profile(self, profile: ObservatoryProfile) -> None:
        """Cache the observatory profile."""
        _atomic_write(self._profile_path, profile.model_dump_json(indent=2))

    def profile(self) -> ObservatoryProfile | None:
        """Return the cached observatory profile, if any."""
        if not self._profile_path.exists():
            return None
        return ObservatoryProfile.model_validate_json(
            self._profile_path.read_text()
        )

    # Plans and outcomes

    def save_plan(self, plan: ObservationPlan) -> None:
        """Make ``plan`` the current plan."""
        _atomic_write(self._plan_path, plan.model_dump_json(indent=2))

    def current_plan(self) -> ObservationPlan | None:
        """Return the current plan, if any."""
        if not self._plan_path.exists():
            return None
        return ObservationPlan.model_validate_json(self._plan_path.read_text())

    def record_outcome(self, outcome: ItemOutcome) -> None:
        """Record the outcome of a plan item."""
        state = self._state()
        key = _key(outcome.plan_id, outcome.item_id)
        state["outcomes"][key] = outcome.model_dump(mode="json")
        self._save_state(state)

    def outcomes(self) -> dict[str, ItemOutcome]:
        """Return outcomes of the current plan's items, by item id."""
        plan = self.current_plan()
        if plan is None:
            return {}
        result = {}
        for data in self._state()["outcomes"].values():
            outcome = ItemOutcome.model_validate(data)
            if outcome.plan_id == plan.plan_id:
                result[outcome.item_id] = outcome
        return result

    def unreported_outcomes(self) -> list[ItemOutcome]:
        """Return outcomes not yet sent to the server, from any plan."""
        state = self._state()
        reported = set(state["reported"])
        return [
            ItemOutcome.model_validate(data)
            for key, data in sorted(state["outcomes"].items())
            if key not in reported
        ]

    def mark_outcomes_reported(self, outcomes: Sequence[ItemOutcome]) -> None:
        """Record that the server has these outcomes."""
        state = self._state()
        reported = set(state["reported"])
        reported.update(_key(o.plan_id, o.item_id) for o in outcomes)
        state["reported"] = sorted(reported)
        self._save_state(state)

    def mark_started(self, plan_id: str, item_id: str) -> None:
        """Record that observing an item has begun."""
        state = self._state()
        started = set(state.get("started", []))
        started.add(_key(plan_id, item_id))
        state["started"] = sorted(started)
        self._save_state(state)

    def started_items(self, plan_id: str) -> set[str]:
        """Return item ids of ``plan_id`` whose observation began."""
        prefix = f"{plan_id}/"
        return {
            key.removeprefix(prefix)
            for key in self._state().get("started", [])
            if key.startswith(prefix)
        }

    # Frames

    def new_frame_path(self, frame_id: UUID) -> Path:
        """Return where a driver should write a new frame."""
        return self.frames_dir / f"{frame_id}.fits"

    def commit_frame(self, record: FrameRecord) -> None:
        """Write a frame's sidecar, making it eligible for upload."""
        path = self.frames_dir / f"{record.frame_id}.json"
        _atomic_write(path, record.model_dump_json(indent=2))

    def pending_frames(self) -> list[FrameRecord]:
        """Return committed frames awaiting upload, oldest first."""
        records = []
        for sidecar in self.frames_dir.glob("*.json"):
            record = self._read_sidecar(sidecar)
            if record and self.new_frame_path(record.frame_id).exists():
                records.append(record)
        return sorted(records, key=lambda r: (r.obs_start, str(r.frame_id)))

    def frame_path(self, frame_id: UUID) -> Path:
        """Return the path of a committed frame."""
        return self.new_frame_path(frame_id)

    def items_with_frames(self, plan_id: str) -> set[str]:
        """Return item ids of ``plan_id`` that have committed frames."""
        return {
            r.item_id for r in self.pending_frames() if r.plan_id == plan_id
        }

    def remove_frame(self, frame_id: UUID) -> None:
        """Delete an uploaded frame and its sidecar."""
        self.new_frame_path(frame_id).unlink(missing_ok=True)
        (self.frames_dir / f"{frame_id}.json").unlink(missing_ok=True)

    def mark_bad(self, frame_id: UUID, reason: str) -> None:
        """Move a frame out of the upload queue, recording why."""
        for suffix in (".fits", ".json"):
            source = self.frames_dir / f"{frame_id}{suffix}"
            if source.exists():
                source.replace(self.bad_dir / source.name)
        (self.bad_dir / f"{frame_id}.reason").write_text(reason + "\n")

    def bad_count(self) -> int:
        """Return the number of frames set aside as bad."""
        return len(list(self.bad_dir.glob("*.fits")))

    def recover(self) -> list[UUID]:
        """Set aside frames left without a valid sidecar by a crash."""
        recovered = []
        for fits in self.frames_dir.glob("*.fits"):
            sidecar = fits.with_suffix(".json")
            if sidecar.exists() and self._read_sidecar(sidecar):
                continue
            frame_id = UUID(fits.stem)
            self.mark_bad(
                frame_id, "incomplete: no valid sidecar after restart"
            )
            recovered.append(frame_id)
        return recovered

    # Stop flag

    def request_stop(self) -> None:
        """Ask a running plan to stop."""
        self._stop_path.touch()

    def stop_requested(self) -> bool:
        """Return whether a stop has been requested."""
        return self._stop_path.exists()

    def clear_stop(self) -> None:
        """Clear a stop request."""
        self._stop_path.unlink(missing_ok=True)

    # Internals

    def _state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"outcomes": {}, "reported": []}
        data: dict[str, Any] = json.loads(self._state_path.read_text())
        return data

    def _save_state(self, state: dict[str, Any]) -> None:
        _atomic_write(self._state_path, json.dumps(state, indent=2))

    @staticmethod
    def _read_sidecar(path: Path) -> FrameRecord | None:
        with contextlib.suppress(OSError, ValidationError, ValueError):
            return FrameRecord.model_validate_json(path.read_text())
        return None
