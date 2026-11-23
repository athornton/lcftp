"""Target tags that tie observer frames to plan items.

A tag looks like ``lcftp-3f9a1c2e-007``: the first eight characters of
the plan id and the item id. Observers put it in frame filenames or the
FITS ``OBJECT`` header so that ``lcftp ingest`` can match frames to plan
items without guessing.
"""

import re
from typing import NamedTuple

__all__ = ["TagRef", "find_tags", "target_tag"]

PLAN_PREFIX_LENGTH = 8

_TAG = re.compile(
    r"(?<![0-9a-z])lcftp-([0-9a-f]{8})-([0-9]+)(?![0-9])", re.IGNORECASE
)


class TagRef(NamedTuple):
    """A tag found in text."""

    plan8: str
    item_id: str


def target_tag(plan_id: str, item_id: str) -> str:
    """Return the target tag for a plan item."""
    return f"lcftp-{plan_id[:PLAN_PREFIX_LENGTH]}-{item_id}".lower()


def find_tags(text: str) -> list[TagRef]:
    """Return the distinct tags in ``text``, in order of appearance."""
    found: list[TagRef] = []
    for match in _TAG.finditer(text):
        ref = TagRef(match.group(1).lower(), match.group(2))
        if ref not in found:
            found.append(ref)
    return found
