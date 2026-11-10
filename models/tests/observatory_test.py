import pytest
from pydantic import ValidationError

from lcftp_models.observatory import ObservatoryCreate, ObservatoryProfile
from lcftp_models.site import FilterSpec, SiteLocation


def test_profile_round_trip(tucson: SiteLocation) -> None:
    profile = ObservatoryProfile(
        id="obs1",
        owner="athornton",
        name="Backyard",
        site=tucson,
        aperture_m=0.05,
        limiting_mag=15.0,
        fov_arcmin=77.0,
        filters=[FilterSpec(name="r", wavelength_nm=620)],
    )
    again = ObservatoryProfile.model_validate_json(profile.model_dump_json())
    assert again == profile
    assert again.driver == "simulated"


def test_create_rejects_extra_fields(tucson: SiteLocation) -> None:
    with pytest.raises(ValidationError):
        ObservatoryCreate.model_validate(
            {
                "name": "x",
                "site": tucson.model_dump(),
                "aperture_m": 0.05,
                "limiting_mag": 15,
                "fov_arcmin": 60,
                "bogus": 1,
            }
        )


@pytest.mark.parametrize("mag", [4.9, 30.1])
def test_create_rejects_bad_limiting_mag(
    tucson: SiteLocation, mag: float
) -> None:
    with pytest.raises(ValidationError):
        ObservatoryCreate(
            name="x", site=tucson, aperture_m=1, limiting_mag=mag, fov_arcmin=1
        )


def test_create_rejects_empty_name(tucson: SiteLocation) -> None:
    with pytest.raises(ValidationError):
        ObservatoryCreate(
            name="", site=tucson, aperture_m=1, limiting_mag=15, fov_arcmin=1
        )
