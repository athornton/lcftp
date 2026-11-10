#!/usr/bin/env python3
"""Regenerate the lcftp-models JSON Schema snapshot."""

import json
from pathlib import Path

from lcftp_models.schema import export_schema

SNAPSHOT = (
    Path(__file__).parent.parent / "models" / "tests" / "schema_snapshot.json"
)


def main() -> None:
    """Write the snapshot file."""
    text = json.dumps(export_schema(), indent=2, sort_keys=True) + "\n"
    SNAPSHOT.write_text(text)


if __name__ == "__main__":
    main()
