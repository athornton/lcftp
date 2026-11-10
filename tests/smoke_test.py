"""Smoke test that the package is installed and versioned."""

from importlib.metadata import version

import lcftp


def test_version() -> None:
    assert isinstance(lcftp.__version__, str)
    assert lcftp.__version__
    assert lcftp.__version__ == version("lcftp")
