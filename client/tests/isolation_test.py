"""The client must not depend on the server or auth packages."""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).parent.parent / "src" / "lcftp_client"
FORBIDDEN = ("lcftp", "lcftp_auth")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.level == 0
        ):
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("path", sorted(SOURCE.rglob("*.py")), ids=str)
def test_no_server_imports(path: Path) -> None:
    assert not _imports(path) & set(FORBIDDEN)
