from importlib.metadata import entry_points

import pytest

from lcftp_client.drivers.loader import load_driver
from lcftp_client.drivers.simulated import SimulatedDriver


def test_loads_simulated() -> None:
    driver = load_driver("simulated", {"seed": 4, "cloud_rate": 0.5})
    assert isinstance(driver, SimulatedDriver)
    assert driver.config.seed == 4


def test_unknown_driver() -> None:
    with pytest.raises(ValueError, match=r"^Unknown telescope driver: nope$"):
        load_driver("nope", {})


def test_entry_point_registered() -> None:
    assert "simulated" in {
        ep.name for ep in entry_points(group="lcftp.drivers")
    }
