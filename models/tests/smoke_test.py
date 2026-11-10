"""Smoke test that the package is installed and versioned."""

from importlib.metadata import version

import lcftp_models


def test_version() -> None:
    assert isinstance(lcftp_models.__version__, str)
    assert lcftp_models.__version__
    assert lcftp_models.__version__ == version("lcftp-models")
