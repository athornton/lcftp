import json
from pathlib import Path

from lcftp_models.schema import export_schema

SNAPSHOT = Path(__file__).parent / "schema_snapshot.json"


def test_schema_snapshot() -> None:
    expected = json.loads(SNAPSHOT.read_text())
    assert export_schema() == expected, (
        "run scripts/update-schema-snapshot.py if this change is intentional"
    )
