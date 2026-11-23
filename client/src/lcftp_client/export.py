"""Export observation plans as CSV for observers and NINA.

The first five columns are the Telescopius observing-list headers that
N.I.N.A.'s "Import targets" accepts; NINA ignores the rest, which tell
observers what to aim for. See ``docs/observers.md``.
"""

import csv
from datetime import datetime
from typing import TextIO

from lcftp_models.plan import ObservationPlan, PlanItem

from .tags import target_tag

__all__ = ["CSV_COLUMNS", "format_dec", "format_ra", "write_plan_csv"]

CSV_COLUMNS = (
    "Familiar Name",
    "Catalogue Entry",
    "Right Ascension (J2000)",
    "Declination (J2000)",
    "Position Angle (East)",
    "Not Before (UTC)",
    "Not After (UTC)",
    "RA (deg)",
    "Dec (deg)",
    "Filter",
    "Total Exposure (s)",
    "Target SNR",
    "Mag",
    "Band",
    "Classification",
    "Priority",
    "Dia Object ID",
    "Plan ID",
    "Item ID",
)
"""Column headers, in order. Never add a bare ``Right Ascension`` header:
NINA's importer would then read it as the declination too."""

_RA_STEPS_PER_DAY = 24 * 3600 * 100
"""Hundredths of a second of time in 24 hours."""


def format_ra(ra_deg: float) -> str:
    """Format right ascension in degrees as ``HHh MMm SS.SSs``."""
    total = round(ra_deg / 15 * 3600 * 100) % _RA_STEPS_PER_DAY
    hours, rest = divmod(total, 3600 * 100)
    minutes, hundredths = divmod(rest, 60 * 100)
    seconds, frac = divmod(hundredths, 100)
    return f"{hours:02d}h {minutes:02d}m {seconds:02d}.{frac:02d}s"


def format_dec(dec_deg: float) -> str:
    """Format declination in degrees as ``±DDd MMm SS.Ss``."""
    total = round(abs(dec_deg) * 3600 * 10)
    sign = "-" if dec_deg < 0 and total > 0 else "+"
    degrees, rest = divmod(total, 3600 * 10)
    minutes, tenths = divmod(rest, 60 * 10)
    seconds, frac = divmod(tenths, 10)
    return f"{sign}{degrees:02d}d {minutes:02d}m {seconds:02d}.{frac}s"


def _utc(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def _row(plan: ObservationPlan, item: PlanItem) -> list[str]:
    c = item.candidate
    hint = item.exposure_hint
    return [
        target_tag(plan.plan_id, item.item_id),
        f"diaObject {c.dia_object_id}",
        format_ra(c.ra),
        format_dec(c.dec),
        "0",
        _utc(item.not_before),
        _utc(item.not_after),
        f"{c.ra:.6f}",
        f"{c.dec:.6f}",
        item.suggested_filter or "",
        _number(hint.total_exposure_s),
        _number(hint.target_snr),
        f"{c.mag:.2f}",
        c.band,
        c.classification or "",
        str(item.priority),
        str(c.dia_object_id),
        plan.plan_id,
        item.item_id,
    ]


def write_plan_csv(plan: ObservationPlan, out: TextIO) -> None:
    """Write ``plan`` as CSV, one row per item in time order."""
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for item in plan.items:
        writer.writerow(_row(plan, item))
