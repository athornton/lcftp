"""Smoke test that the package is installed and versioned."""

from importlib.metadata import version

import lcftp_client


def test_version() -> None:
    assert isinstance(lcftp_client.__version__, str)
    assert lcftp_client.__version__
    assert lcftp_client.__version__ == version("lcftp-client")
