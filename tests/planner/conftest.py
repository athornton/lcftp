"""Keep astropy offline for planner tests."""

import pytest
from astropy.utils import iers


@pytest.fixture(autouse=True, scope="session")
def _offline_iers() -> None:
    iers.conf.auto_download = False
    iers.conf.auto_max_age = None
