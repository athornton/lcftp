"""Write small FITS files with chosen header cards, for ingest tests."""

from pathlib import Path

import numpy as np
from astropy.io import fits

__all__ = ["write_fits"]


def write_fits(path: Path, **cards: str | float) -> Path:
    """Write a 4x4 frame with ``cards`` in the primary header.

    Card names are given with ``_`` for ``-`` (``DATE_OBS``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    hdu = fits.PrimaryHDU(np.zeros((4, 4), dtype=np.int16))
    for key, value in cards.items():
        hdu.header[key.replace("_", "-")] = value
    hdu.writeto(path)
    return path
