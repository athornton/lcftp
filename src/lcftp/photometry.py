"""Photometric conversions."""

import math

__all__ = ["AB_ZERO_POINT_NJY", "flux_njy_to_ab_mag"]

AB_ZERO_POINT_NJY = 31.4
"""AB magnitude of a 1 nJy source: 2.5 log10(3631 Jy / 1 nJy)."""


def flux_njy_to_ab_mag(flux_njy: float) -> float:
    """Convert a flux in nanojanskys to an AB magnitude.

    Raises
    ------
    ValueError
        If the flux is not positive.
    """
    if flux_njy <= 0:
        msg = f"Flux must be positive, got {flux_njy}"
        raise ValueError(msg)
    return AB_ZERO_POINT_NJY - 2.5 * math.log10(flux_njy)
