"""Frame pointing comes from the image centre, not the WCS reference."""

import pytest
from astropy.coordinates import SkyCoord
from astropy.io import fits

from lcftp_driver_seestar.control import GotoResult
from lcftp_driver_seestar.driver import _celestial, _pointing

GOTO = GotoResult(
    state="complete",
    ra_hours=1.0,
    dec_deg=2.0,
    dist_deg=0.01,
    error=None,
)

# Cards from a Seestar S30 Plus stack of M 31 (first light, 2026-11-24).
# The solver puts the reference pixel well away from the image centre,
# and the header RA/DEC is the mount's idea of the target.
M31_STACK = {
    "NAXIS": 3,
    "NAXIS1": 1440,
    "NAXIS2": 2560,
    "NAXIS3": 3,
    "RA": 11.120835,
    "DEC": 41.421667,
    "CTYPE1": "RA---TAN-SIP",
    "CTYPE2": "DEC--TAN-SIP",
    "CRVAL1": 10.4570230902,
    "CRVAL2": 40.6131660567,
    "CRPIX1": 1295.879557292,
    "CRPIX2": 925.558598836,
    "CD1_1": 0.00026081694812,
    "CD1_2": 0.000989358696925,
    "CD2_1": -0.00098876870749,
    "CD2_2": 0.0002598303206425,
    "A_ORDER": 2,
    "B_ORDER": 2,
    "A_0_2": 2.53443990422e-07,
    "A_1_1": -3.46670631808e-07,
    "A_2_0": -6.35612503045e-07,
    "B_0_2": 2.49784451987e-07,
    "B_1_1": -7.38053311995e-08,
    "B_2_0": 1.241499351275e-06,
}
# Centre of that image as solved by astrometry (pixel 720.5, 1280.5).
M31_CENTRE = SkyCoord(10.725140, 41.274193, unit="deg")


def test_pointing_is_wcs_image_centre_not_crval() -> None:
    header = fits.Header(M31_STACK)
    ra, dec = _pointing(header, GOTO)
    offset = SkyCoord(ra, dec, unit="deg").separation(M31_CENTRE)
    assert offset.arcsec == pytest.approx(0, abs=1)


def test_pointing_falls_back_to_header_ra_dec() -> None:
    header = fits.Header({"NAXIS1": 1440, "NAXIS2": 2560})
    header["RA"] = 11.1
    header["DEC"] = 41.4
    assert _pointing(header, GOTO) == (11.1, 41.4)


def test_pointing_falls_back_to_goto() -> None:
    assert _pointing(fits.Header(), GOTO) == (15.0, 2.0)


def test_colour_stack_with_sip_has_celestial_wcs() -> None:
    assert _celestial(fits.Header(M31_STACK))
