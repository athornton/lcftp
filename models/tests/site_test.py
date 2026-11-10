import pytest
from pydantic import ValidationError

from lcftp_models.site import FilterSpec, ObservingConstraints, SiteLocation


def test_site_rejects_bad_latitude() -> None:
    with pytest.raises(ValidationError):
        SiteLocation(latitude=91, longitude=0, elevation_m=0, timezone="UTC")


def test_site_rejects_bad_longitude() -> None:
    with pytest.raises(ValidationError):
        SiteLocation(
            latitude=0, longitude=-180.5, elevation_m=0, timezone="UTC"
        )


def test_site_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        SiteLocation(
            latitude=0, longitude=0, elevation_m=0, timezone="Mars/Olympus"
        )


def test_filter_requires_positive_wavelength() -> None:
    with pytest.raises(ValidationError):
        FilterSpec(name="r", wavelength_nm=0)


def test_constraint_defaults() -> None:
    c = ObservingConstraints()
    assert c.min_altitude_deg == 30.0
    assert c.max_airmass == 2.0
    assert c.min_moon_separation_deg == 30.0


def test_site_is_frozen(tucson: SiteLocation) -> None:
    with pytest.raises(ValidationError):
        tucson.latitude = 0  # type: ignore[misc]
