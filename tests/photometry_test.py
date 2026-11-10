import pytest

from lcftp.photometry import flux_njy_to_ab_mag


def test_zero_point() -> None:
    assert flux_njy_to_ab_mag(3.631e12) == pytest.approx(0.0, abs=1e-3)


def test_one_nanojansky() -> None:
    assert flux_njy_to_ab_mag(1.0) == pytest.approx(31.4)


def test_ten_thousand_nanojansky() -> None:
    assert flux_njy_to_ab_mag(1e4) == pytest.approx(21.4)


@pytest.mark.parametrize("flux", [0.0, -5.0])
def test_non_positive_flux(flux: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        flux_njy_to_ab_mag(flux)
