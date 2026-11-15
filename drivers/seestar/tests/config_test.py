from importlib.metadata import entry_points

import pytest
from pydantic import ValidationError

from lcftp_driver_seestar.config import SeestarDriverConfig

SITE = {"site_lat_deg": 40.0, "site_lon_deg": -111.0}


def test_defaults() -> None:
    config = SeestarDriverConfig.model_validate(SITE)
    assert config.host is None
    assert config.key_path is None
    assert config.sub_exposure_s == 10
    assert config.default_total_exposure_s == 60
    assert config.goto_timeout_s == 180
    assert config.max_pointing_error_fov == 0.25
    assert config.poll_interval_s == 5
    assert config.sun_altitude_limit_deg == -6
    assert config.site_elevation_m == 0


def test_rejects_unsupported_sub_exposure() -> None:
    with pytest.raises(ValidationError):
        SeestarDriverConfig.model_validate({**SITE, "sub_exposure_s": 15})


def test_requires_site() -> None:
    with pytest.raises(ValidationError):
        SeestarDriverConfig.model_validate({})


def test_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        SeestarDriverConfig.model_validate({**SITE, "hots": "10.0.0.1"})


def test_entry_point_registered() -> None:
    names = {ep.name for ep in entry_points(group="lcftp.drivers")}
    assert "seestar" in names


def test_longitude_is_east_positive_within_180() -> None:
    with pytest.raises(ValidationError):
        SeestarDriverConfig.model_validate({**SITE, "site_lon_deg": 249.0})
