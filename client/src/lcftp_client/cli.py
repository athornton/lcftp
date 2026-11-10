"""Command-line interface for the LCFTP observatory client."""

import asyncio
import signal
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import click
from pydantic import HttpUrl, SecretStr

from .api import ServerClient
from .config import ClientConfig, default_config_path, load_config, save_config
from .session import LcftpClient
from .spool import Spool

__all__ = ["main", "make_client", "next_window"]

UPLOAD_RETRY = timedelta(minutes=15)


def make_client(config: ClientConfig) -> LcftpClient:
    """Create the client (replaced in tests)."""
    return LcftpClient(config)


def next_window(
    now: datetime, start: time, end: time, tz: str
) -> tuple[datetime, datetime]:
    """Return the current or next nightly availability window in UTC."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    for days in (-1, 0, 1):
        day = local.date() + timedelta(days=days)
        begin = datetime.combine(day, start, zone)
        finish = datetime.combine(day, end, zone)
        if finish <= begin:
            finish += timedelta(days=1)
        if now < finish.astimezone(UTC):
            return max(now, begin.astimezone(UTC)), finish.astimezone(UTC)
    msg = "No window found"  # pragma: no cover
    raise AssertionError(msg)  # pragma: no cover


def _parse_time(value: str) -> datetime:
    when = datetime.fromisoformat(value)
    if when.tzinfo is None:
        msg = f"{value} needs a timezone, e.g. 2026-11-10T03:00:00Z"
        raise click.BadParameter(msg)
    return when.astimezone(UTC)


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Client configuration file.",
)
@click.pass_context
def main(ctx: click.Context, config_path: Path | None) -> None:
    """Los Cumbres For The People observatory client."""
    ctx.obj = config_path or default_config_path()


def _config(ctx: click.Context) -> ClientConfig:
    path: Path = ctx.obj
    return load_config(path)


@main.command()
@click.option("--server", required=True, help="LCFTP server URL.")
@click.option("--token", required=True, help="Device token from the web site.")
@click.option(
    "--spool",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path.home() / ".local" / "share" / "lcftp" / "spool",
    show_default=True,
)
@click.pass_context
def init(ctx: click.Context, server: str, token: str, spool: Path) -> None:
    """Register this client with the server."""

    async def whoami() -> ClientConfig:
        client = ServerClient(server, token)
        try:
            profile = await client.whoami()
        finally:
            await client.aclose()
        Spool(spool).save_profile(profile)
        return ClientConfig(
            server_url=HttpUrl(server),
            token=SecretStr(token),
            observatory_id=profile.id,
            spool_dir=spool,
            driver=profile.driver,
        )

    config = asyncio.run(whoami())
    save_config(config, ctx.obj)
    click.echo(f"Configured for observatory {config.observatory_id}")


@main.command()
@click.option(
    "--start", required=True, help="Window start (ISO 8601 with tz)."
)
@click.option("--end", required=True, help="Window end (ISO 8601 with tz).")
@click.pass_context
def plan(ctx: click.Context, start: str, end: str) -> None:
    """Request a plan for an availability window."""

    async def request() -> str:
        async with make_client(_config(ctx)) as client:
            result = await client.request_plan(
                _parse_time(start), _parse_time(end)
            )
        return f"Plan {result.plan_id}: {len(result.items)} targets"

    click.echo(asyncio.run(request()))


@main.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Run the stored plan."""

    async def execute() -> None:
        async with make_client(_config(ctx)) as client:
            await client.run()

    asyncio.run(execute())


@main.command()
@click.pass_context
def upload(ctx: click.Context) -> None:
    """Upload pending frames and outcomes."""

    async def flush() -> str:
        async with make_client(_config(ctx)) as client:
            return (await client.upload()).model_dump_json()

    click.echo(asyncio.run(flush()))


@main.command()
@click.pass_context
def stop(ctx: click.Context) -> None:
    """Ask a running plan to stop and park the telescope."""
    Spool(_config(ctx).spool_dir).request_stop()
    click.echo("Stop requested")


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Print local status as JSON."""
    client = make_client(_config(ctx))
    click.echo(client.status().model_dump_json())


@main.command()
@click.pass_context
def daemon(ctx: click.Context) -> None:
    """Observe every night in the configured window, then upload."""
    config = _config(ctx)
    if config.nightly_start is None or config.nightly_end is None:
        msg = "Set nightly_start and nightly_end in the configuration"
        raise click.ClickException(msg)
    asyncio.run(_daemon(config, config.nightly_start, config.nightly_end))


async def _daemon(config: ClientConfig, start: time, end: time) -> None:
    stopping = asyncio.Event()
    async with make_client(config) as client:
        loop = asyncio.get_running_loop()

        def on_signal() -> None:
            client.stop()
            stopping.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, on_signal)
        profile = await client.profile()
        while not stopping.is_set():
            begin, finish = next_window(
                client.clock.now(), start, end, profile.site.timezone
            )
            if await _wait(stopping, begin - client.clock.now()):
                break
            summary = await client.night_once(begin, finish)
            click.echo(summary.model_dump_json())
            while summary.remaining and not stopping.is_set():
                if await _wait(stopping, UPLOAD_RETRY):
                    break
                summary = await client.upload()
                click.echo(summary.model_dump_json())


async def _wait(stopping: asyncio.Event, delay: timedelta) -> bool:
    """Wait for ``delay``; return True if stopping was requested."""
    try:
        await asyncio.wait_for(stopping.wait(), max(delay.total_seconds(), 0))
    except TimeoutError:
        return False
    return True
