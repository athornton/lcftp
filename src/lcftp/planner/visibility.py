"""Target visibility from an observatory, via astroplan."""

import warnings

import astropy.units as u
import numpy as np
from astroplan import (
    AirmassConstraint,
    AltitudeConstraint,
    AtNightConstraint,
    MoonSeparationConstraint,
    Observer,
)
from astropy.coordinates import (
    EarthLocation,
    NonRotationTransformationWarning,
    SkyCoord,
)
from astropy.time import Time
from numpy.typing import NDArray

from lcftp_models.site import ObservingConstraints, SiteLocation

__all__ = ["observable_mask", "to_observer"]


def to_observer(site: SiteLocation) -> Observer:
    """Convert a site to an astroplan observer."""
    location = EarthLocation.from_geodetic(
        lon=site.longitude * u.deg,
        lat=site.latitude * u.deg,
        height=site.elevation_m * u.m,
    )
    return Observer(location=location, timezone=site.timezone)


def observable_mask(
    site: SiteLocation,
    constraints: ObservingConstraints,
    coords: SkyCoord,
    times: Time,
) -> NDArray[np.bool_]:
    """Return a ``(n_targets, n_times)`` mask of when targets are observable.

    Observable means above the minimum altitude, below the maximum
    airmass, far enough from the Moon, and during astronomical night.
    """
    observer = to_observer(site)
    checks = [
        AltitudeConstraint(min=constraints.min_altitude_deg * u.deg),
        AirmassConstraint(max=constraints.max_airmass),
        MoonSeparationConstraint(
            min=constraints.min_moon_separation_deg * u.deg
        ),
        AtNightConstraint.twilight_astronomical(),
    ]
    mask = np.ones((len(coords), len(times)), dtype=bool)
    with warnings.catch_warnings():
        # astroplan's Moon separation compares GCRS and ICRS positions; the
        # resulting warning is about sub-arcsecond effects, irrelevant here.
        warnings.simplefilter("ignore", NonRotationTransformationWarning)
        for check in checks:
            result = check(
                observer, coords, times=times, grid_times_targets=True
            )
            mask &= np.broadcast_to(np.asarray(result, dtype=bool), mask.shape)
    return mask
