"""Client and models runtime requirements must be in the RSP kernel."""

from importlib.metadata import requires
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

RSP_LIST = Path(__file__).parent / "data" / "rsp-kernel-packages.txt"

# Conda package names that differ from the PyPI distribution name.
CONDA_ALIASES = {"matplotlib": "matplotlib-base"}


def _rsp_packages() -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in RSP_LIST.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, version = line.partition("==")
        packages[canonicalize_name(name)] = version
    return packages


def _requirements(dist: str) -> list[Requirement]:
    reqs = [Requirement(r) for r in requires(dist) or []]
    return [
        r
        for r in reqs
        if r.marker is None
        and not canonicalize_name(r.name).startswith("lcftp")
    ]


@pytest.mark.parametrize("dist", ["lcftp-client", "lcftp-models"])
def test_requirements_in_rsp_kernel(dist: str) -> None:
    rsp = _rsp_packages()
    problems = []
    for req in _requirements(dist):
        name = canonicalize_name(CONDA_ALIASES.get(req.name, req.name))
        version = rsp.get(name)
        if version is None:
            problems.append(f"{req.name}: not in the RSP kernel")
        elif not req.specifier.contains(version, prereleases=True):
            problems.append(
                f"{req.name}: RSP has {version}, we need {req.specifier}"
            )
    assert not problems, "\n".join(problems)


def test_rsp_python_is_supported() -> None:
    python = _rsp_packages()["python"]
    assert tuple(int(p) for p in python.split(".")[:2]) >= (3, 13)
