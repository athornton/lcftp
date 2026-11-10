"""Build an observation plan for one observatory and window."""

import math
from collections.abc import Mapping, Sequence

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.time import Time

from lcftp_models.candidate import Candidate
from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.plan import AvailabilityWindow, ExposureHint, PlanItem

from .settings import PlannerSettings
from .visibility import observable_mask

__all__ = ["CAP_PENALTY", "plan_observations"]

CAP_PENALTY = 100.0
"""Score penalty for targets already assigned to enough observatories."""

RECENCY_WEIGHT = 0.1
"""Score lost per hour since the latest alert."""


def _score(
    candidate: Candidate,
    profile: ObservatoryProfile,
    window: AvailabilityWindow,
    assignment_counts: Mapping[int, int],
    settings: PlannerSettings,
) -> float:
    age_h = (window.start - candidate.latest_alert_time).total_seconds() / 3600
    score = (profile.limiting_mag - candidate.mag) - RECENCY_WEIGHT * age_h
    if (
        assignment_counts.get(candidate.dia_object_id, 0)
        >= settings.observatory_cap
    ):
        score -= CAP_PENALTY
    return score


def _filter(profile: ObservatoryProfile, band: str) -> str | None:
    for spec in profile.filters:
        if spec.name == band:
            return spec.name
    return profile.filters[0].name if profile.filters else None


def plan_observations(
    profile: ObservatoryProfile,
    window: AvailabilityWindow,
    candidates: Sequence[Candidate],
    assignment_counts: Mapping[int, int],
    settings: PlannerSettings,
) -> list[PlanItem]:
    """Choose and schedule targets for an availability window.

    Targets are taken greedily in descending score order and each is put
    in the earliest free slot during which it stays observable from start
    to end. The result is sorted by start time.
    """
    usable = [c for c in candidates if c.mag <= profile.limiting_mag]
    n_steps = int((window.end - window.start) / settings.grid_step)
    k = math.ceil(settings.slot / settings.grid_step)
    if not usable or n_steps < k:
        return []
    times = Time(
        [window.start + i * settings.grid_step for i in range(n_steps + 1)]
    )
    coords = SkyCoord(
        ra=[c.ra for c in usable] * u.deg, dec=[c.dec for c in usable] * u.deg
    )
    mask = observable_mask(profile.site, profile.constraints, coords, times)

    scores = [
        _score(c, profile, window, assignment_counts, settings) for c in usable
    ]
    order = sorted(range(len(usable)), key=lambda i: -scores[i])
    used = np.zeros(n_steps, dtype=bool)
    placed: list[tuple[int, int]] = []
    for idx in order:
        row = mask[idx]
        for start in range(n_steps - k + 1):
            if (
                row[start : start + k + 1].all()
                and not used[start : start + k].any()
            ):
                used[start : start + k] = True
                placed.append((start, idx))
                break

    rank = {idx: r for r, (_, idx) in enumerate(placed)}
    items = []
    for n, (start, idx) in enumerate(sorted(placed)):
        candidate = usable[idx]
        begin = window.start + start * settings.grid_step
        items.append(
            PlanItem(
                item_id=f"{n:03d}",
                candidate=candidate,
                not_before=begin,
                not_after=begin + k * settings.grid_step,
                suggested_filter=_filter(profile, candidate.band),
                exposure_hint=ExposureHint(
                    total_exposure_s=settings.default_exposure_s
                ),
                priority=rank[idx],
            )
        )
    return items
