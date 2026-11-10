"""Command-line entry point for the LCFTP server."""

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

import click
import uvicorn

__all__ = ["main"]


@click.group()
def main() -> None:
    """Los Cumbres For The People server."""


@main.command()
@click.option("--host", default="0.0.0.0", show_default=True)  # noqa: S104
@click.option("--port", default=8080, show_default=True, type=int)
def web(host: str, port: int) -> None:
    """Run the web application."""
    uvicorn.run("lcftp.main:create_app", factory=True, host=host, port=port)


@main.command()
def worker() -> None:
    """Run the broker ingest worker."""
    from .config import Settings  # noqa: PLC0415
    from .ingest.worker import run_worker  # noqa: PLC0415

    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker(Settings()))


@main.command()
@click.option(
    "--config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to alembic.ini (default: the one shipped with LCFTP).",
)
def migrate(config: Path | None) -> None:
    """Upgrade the database schema to the latest version."""
    ini = config or Path(os.environ.get("LCFTP_ALEMBIC_CONFIG", "alembic.ini"))
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), "upgrade", "head"],
        check=True,
    )
