from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from lcftp_models.plan import AvailabilityWindow

START = datetime(2026, 11, 10, 5, 0, tzinfo=UTC)


def test_window_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        AvailabilityWindow(start=START, end=START)


def test_window_rejects_more_than_24_hours() -> None:
    with pytest.raises(ValidationError):
        AvailabilityWindow(
            start=START, end=START + timedelta(hours=24, seconds=1)
        )


def test_window_allows_exactly_24_hours() -> None:
    window = AvailabilityWindow(start=START, end=START + timedelta(hours=24))
    assert window.end - window.start == timedelta(hours=24)


def test_window_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError):
        AvailabilityWindow.model_validate(
            {"start": "2026-11-10T05:00:00", "end": "2026-11-10T15:00:00"}
        )


def test_window_normalises_to_utc() -> None:
    window = AvailabilityWindow.model_validate(
        {
            "start": "2026-11-10T05:00:00-07:00",
            "end": "2026-11-10T15:00:00-07:00",
        }
    )
    assert window.start == datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    assert window.start.tzinfo is UTC
