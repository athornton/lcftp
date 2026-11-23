"""Command-line interface for the LCFTP observatory client."""

import asyncio
import io
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from functools import partial
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import click
from pydantic import HttpUrl, SecretStr, ValidationError

from lcftp_models.plan import ObservationPlan

from .api import ServerClient
from .clock import Clock
from .config import ClientConfig, default_config_path, load_config, save_config
from .export import write_plan_csv
from .ingest.finalise import finalise_plan
from .ingest.ingester import IngestReport, ingest_dir
from .session import LcftpClient
from .spool import Spool
from .uploader import UploadSummary

__all__ = ["daemon_loop", "main", "make_client", "next_window"]

logger = logging.getLogger(__name__)

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
            _install_stop_handlers(client, asyncio.Event())
            await client.run()

    asyncio.run(execute())


@main.command()
@click.argument(
    "path", required=False, type=click.Path(dir_okay=False, path_type=Path)
)
@click.option(
    "--plan-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Export this saved plan JSON instead of the current plan.",
)
@click.pass_context
def export(
    ctx: click.Context, path: Path | None, plan_file: Path | None
) -> None:
    """Write a plan as CSV (NINA-importable) to PATH, or to stdout."""
    spool = Spool(_config(ctx).spool_dir)
    if plan_file is not None:
        try:
            plan = ObservationPlan.model_validate_json(plan_file.read_text())
        except ValidationError as e:
            msg = f"{plan_file} is not an lcftp plan: {e}"
            raise click.ClickException(msg) from e
        spool.archive_plan(plan)
    else:
        current = spool.current_plan()
        if current is None:
            msg = "There is no plan to export; run lcftp plan first"
            raise click.ClickException(msg)
        plan = current
        spool.archive_plan(plan)
    if path is None:
        buf = io.StringIO()
        write_plan_csv(plan, buf)
        click.echo(buf.getvalue(), nl=False)
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        write_plan_csv(plan, f)
    click.echo(f"Wrote {len(plan.items)} targets to {path}", err=True)


def _echo_report(report: IngestReport) -> None:
    for r in report.results:
        parts = [str(r.status), str(r.path)]
        parts += [p for p in (r.tag, r.detail, r.warning) if p]
        click.echo("  ".join(parts))
    totals = ", ".join(f"{n} {s}" for s, n in sorted(report.counts().items()))
    click.echo(f"Total: {totals or 'no FITS files found'}")


@main.command()
@click.argument(
    "directory",
    required=False,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--item", "item_tag", help="Target tag for frames that carry none."
)
@click.option(
    "--dry-run", is_flag=True, help="Report without copying anything."
)
@click.option(
    "--force",
    is_flag=True,
    help="Accept frames outside the plan window or away from the target.",
)
@click.option(
    "--final",
    is_flag=True,
    help="Then record outcomes for the plan; run once you are done.",
)
@click.option("--plan", "plan8", help="Plan id (or its first 8 characters).")
@click.pass_context
def ingest(  # noqa: PLR0917
    ctx: click.Context,
    directory: Path | None,
    item_tag: str | None,
    dry_run: bool,  # noqa: FBT001
    force: bool,  # noqa: FBT001
    final: bool,  # noqa: FBT001
    plan8: str | None,
) -> None:
    """Queue frames taken with your own software for upload.

    Frames are matched to plan items by the target tag in their file
    name or OBJECT header. Originals are copied, never changed.
    """
    if final and not plan8:
        msg = "--final needs --plan"
        raise click.UsageError(msg)
    if directory is None and not final:
        msg = "Give a directory of frames (or --final --plan to finish)"
        raise click.UsageError(msg)
    spool = Spool(_config(ctx).spool_dir)
    profile = spool.profile()
    if profile is None:
        msg = "No observatory profile cached; run lcftp plan first"
        raise click.ClickException(msg)
    if directory is not None:
        _echo_report(
            ingest_dir(
                spool,
                profile,
                directory,
                item_tag=item_tag,
                force=force,
                dry_run=dry_run,
            )
        )
    if final and plan8 and not dry_run:
        try:
            plan = spool.find_plan(plan8)
        except LookupError as e:
            raise click.ClickException(str(e)) from e
        recorded = finalise_plan(spool, plan)
        observed = sum(o.status == "observed" for o in recorded)
        click.echo(
            f"Finalised {plan.plan_id}: {observed} observed, "
            f"{len(recorded) - observed} skipped"
        )


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


class DaemonClient(Protocol):
    """What the daemon loop needs from `LcftpClient`."""

    @property
    def clock(self) -> Clock:
        """The client's clock."""

    async def night_once(
        self, start: datetime, end: datetime
    ) -> UploadSummary:
        """Plan, observe and upload for one window."""

    async def upload(self) -> UploadSummary:
        """Upload pending frames."""


async def daemon_loop(
    client: DaemonClient,
    start: time,
    end: time,
    tz: str,
    wait: Callable[[timedelta], Awaitable[bool]],
) -> None:
    """Observe once per nightly window, retrying uploads in between.

    ``wait`` sleeps for a duration and returns True if the daemon should
    stop. A failed night is logged and retried after `UPLOAD_RETRY`; it
    never ends the daemon.
    """
    earliest = client.clock.now()
    while True:
        now = client.clock.now()
        begin, finish = next_window(max(now, earliest), start, end, tz)
        if await wait(begin - now):
            return
        try:
            summary = await client.night_once(begin, finish)
        except Exception:
            logger.exception("Night starting %s failed", begin)
            if await wait(UPLOAD_RETRY):
                return
            continue
        click.echo(summary.model_dump_json())
        earliest = finish + timedelta(seconds=1)
        while summary.remaining:
            if await wait(UPLOAD_RETRY):
                return
            try:
                summary = await client.upload()
            except Exception:
                logger.exception("Upload retry failed")
                continue
            click.echo(summary.model_dump_json())


def _install_stop_handlers(
    client: LcftpClient, stopping: asyncio.Event
) -> None:
    loop = asyncio.get_running_loop()

    def on_signal() -> None:
        client.stop()
        stopping.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, on_signal)


async def _daemon(config: ClientConfig, start: time, end: time) -> None:
    stopping = asyncio.Event()
    async with make_client(config) as client:
        _install_stop_handlers(client, stopping)
        profile = await client.profile()
        await daemon_loop(
            client, start, end, profile.site.timezone, partial(_wait, stopping)
        )


async def _wait(stopping: asyncio.Event, delay: timedelta) -> bool:
    """Wait for ``delay``; return True if stopping was requested."""
    try:
        await asyncio.wait_for(stopping.wait(), max(delay.total_seconds(), 0))
    except TimeoutError:
        return stopping.is_set()
    return True
