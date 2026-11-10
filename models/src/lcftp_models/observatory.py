"""Observatory registration models."""

from typing import Annotated

from pydantic import Field

from ._base import ContractModel
from .site import FilterSpec, ObservingConstraints, SiteLocation

__all__ = ["ObservatoryCreate", "ObservatoryProfile"]


class ObservatoryCreate(ContractModel):
    """Data supplied by a user to register an observatory."""

    name: Annotated[str, Field(min_length=1, max_length=100)]
    site: SiteLocation
    aperture_m: Annotated[float, Field(gt=0)]
    limiting_mag: Annotated[float, Field(ge=5, le=30)]
    """Faintest magnitude observable under typical conditions."""

    fov_arcmin: Annotated[float, Field(gt=0)]
    """Field of view (longest dimension) in arcminutes."""

    filters: list[FilterSpec] = Field(default_factory=list)
    driver: Annotated[str, Field(min_length=1)] = "simulated"
    """Entry-point name of the telescope driver the client uses."""

    constraints: ObservingConstraints = Field(
        default_factory=ObservingConstraints
    )


class ObservatoryProfile(ObservatoryCreate):
    """A registered observatory."""

    id: str
    owner: str
    """GitHub login of the owning user."""
