import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path

from pydantic import HttpUrl, SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from lcftp.brokers.testing import FakeBroker, make_candidate
from lcftp.db.session import create_sessionmaker
from lcftp.ingest.worker import run_ingest_once
from lcftp_client.config import ClientConfig, save_config
from tests.e2e.conftest import Stack
from tests.e2e.night_test import START
from tests.support.factories import make_device, make_observatory, make_user

NOTEBOOK = (
    Path(__file__).parent.parent.parent
    / "client"
    / "notebooks"
    / "lcftp-quickstart.ipynb"
)


async def test_quickstart_notebook(
    stack: Stack, engine: AsyncEngine, db_session: AsyncSession, tmp_path: Path
) -> None:
    user = await make_user(db_session)
    await make_observatory(db_session, user, "obs1", limiting_mag=15)
    headers = await make_device(db_session, "obs1")
    await db_session.commit()
    candidates = [
        make_candidate(
            2000 + i,
            ra=20 + 15 * i,
            dec=15,
            mag=12,
            when=START - timedelta(hours=1),
        )
        for i in range(6)
    ]
    await run_ingest_once(
        [FakeBroker(candidates)],
        create_sessionmaker(engine),
        stack.settings,
        START,
    )
    config_path = tmp_path / "client.toml"
    save_config(
        ClientConfig(
            server_url=HttpUrl(stack.url),
            token=SecretStr(headers["Authorization"].removeprefix("Bearer ")),
            observatory_id="obs1",
            spool_dir=tmp_path / "spool",
            driver_config={"image_size": 64},
        ),
        config_path,
    )
    env = {
        **os.environ,
        "LCFTP_CLIENT_CONFIG": str(config_path),
        "LCFTP_NOTEBOOK_START": START.isoformat(),
        "LCFTP_NOTEBOOK_FAST_FORWARD": "1",
        "MPLBACKEND": "Agg",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "--nbmake",
        "--nbmake-timeout=300",
        str(NOTEBOOK),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    assert process.returncode == 0, output.decode()[-3000:]
    assert not list((tmp_path / "spool" / "frames").glob("*.fits"))
