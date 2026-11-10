"""Smoke test that the package is installed and versioned."""

from importlib.metadata import version

import lcftp_auth


def test_version() -> None:
    assert isinstance(lcftp_auth.__version__, str)
    assert lcftp_auth.__version__
    assert lcftp_auth.__version__ == version("lcftp-auth")
