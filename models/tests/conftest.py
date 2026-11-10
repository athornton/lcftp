"""Shared fixtures for lcftp-models tests."""

from datetime import UTC, datetime

import pytest

from lcftp_models.candidate import BrokerRef, Candidate
from lcftp_models.site import SiteLocation


@pytest.fixture
def tucson() -> SiteLocation:
    return SiteLocation(
        latitude=32.23,
        longitude=-110.95,
        elevation_m=728.0,
        timezone="America/Phoenix",
    )


@pytest.fixture
def candidate() -> Candidate:
    when = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)
    return Candidate(
        dia_object_id=123,
        sources=[BrokerRef(broker="alerce", broker_object_id="123")],
        latest_alert_id="12300",
        latest_alert_time=when,
        ra=30.0,
        dec=-5.0,
        mag=17.5,
        band="r",
        first_seen=when,
        last_updated=when,
    )
