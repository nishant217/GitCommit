"""Click CLI for gitcommit-bot."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
import yaml

from gitcommit import __version__
from gitcommit import git_ops
from gitcommit.config import (
    DEFAULT_DATA_DIR,
    Config,
    default_config_path,
    load_config,
    save_config,
)
from gitcommit.logger import get_logger, read_log_tail, setup_logging
from gitcommit.planner import ensure_month_plan, set_paused, status_snapshot
from gitcommit.scheduler import (
    daily_orchestrate,
    handle_startup_catchup,
    install_cron,
    uninstall_cron,
)
from gitcommit.service import Service


def _load(ctx_config: str | None) -> Config:
    return load_config(ctx_config)


@click.group()
@click.version_option(__version__, prog_name="gitcommit")
@click.option(
    "--config",
    "config_path",
    envvar="GITCOMMIT_CONFIG",
    default=None,
    help="Path to config.yaml",
)
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging")
@click.pass_context
def main(ctx: click.Context, config_path: str | None, verbose: bool) -> None:
    """Randomized daily GitHub contribution commits."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["verbose"] = verbose


@main.command()
@click.option("--repo", prompt="Local repo path", default="~/git-activity")
@click.option("--branch", prompt="Branch", default="main")
@click.option("--name", "author_name", prompt="Git author name")
@click.option("--email", "author_email", prompt="Git author email")
@click.option("--timezone", prompt="Timezone (IANA)", default="Asia/Kolkata")
@click.option("--remote", "remote_url", prompt="Remote URL (SSH or HTTPS)", default="")
@click.option("--install-cron/--no-install-cron", default=True, help="Install daily cron at 00:05")
@click.option("--cron-hour", default=0, show_default=True)
@click.option("--cron-minute", default=5, show_default=True)
@click.pass_context
def init(
    ctx: click.Context,
    repo: str,
    branch: str,
    author_name: str,
    author_email: str,
    timezone: str,
    remote_url: str,
    install_cron: bool,
    cron_hour: int,
    cron_minute: int,
) -> None:
    """Set up config, repository, and optional cron entry."""
    data_dir = DEFAULT_DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)

    example = Path(__file__).resolve().parent.parent / "config.example.yaml"
    messages = []
    if example.is_file():
        with open(example, encoding="utf-8") as fh:
            sample = yaml.safe_load(fh) or {}
            messages = list(sample.get("commit_messages") or [])

    cfg = Config(
        repo_path=Path(os.path.expanduser(repo)),
        branch=branch,
        author_name=author_name,
        author_email=author_email,
        timezone=timezone,
        remote_url=remote_url or "",
        commit_messages=messages,
        data_dir=data_dir,
    )
    config_file = save_config(cfg, default_config_path(data_dir))
    setup_logging(cfg.log_path, verbose=ctx.obj.get("verbose", False))
    log = get_logger()
    log.info("Wrote config to %s", config_file)

    git_ops.ensure_repo(cfg)
    ensure_month_plan(cfg)
    click.echo(f"Config: {config_file}")
    click.echo(f"Repo:   {cfg.repo_path}")
    click.echo(f"Data:   {cfg.data_dir}")

    if install_cron:
        try:
            line = install_cron(cfg, hour=cron_hour, minute=cron_minute)
            click.echo(f"Cron installed:\n  {line}")
        except Exception as exc:
            click.echo(f"Cron install failed ({exc}). Install manually — see README.", err=True)

    click.echo(
        "\nTip: export GITHUB_TOKEN=ghp_... for HTTPS remotes, "
        "or use an SSH remote with your key loaded."
    )
    click.echo("Commands: gitcommit status | run | dry-run | pause | resume | dashboard")


@main.command()
@click.option("--now", "immediate", is_flag=True, help="Run commits immediately (skip wait)")
@click.option("--catch-up/--no-catch-up", default=True, help="Handle missed schedule on start")
@click.pass_context
def run(ctx: click.Context, immediate: bool, catch_up: bool) -> None:
    """Plan today and schedule/run the commit job (cron entry point)."""
    cfg = _load(ctx.obj.get("config_path"))
    setup_logging(cfg.log_path, verbose=ctx.obj.get("verbose", False))
    if catch_up and not immediate:
        handle_startup_catchup(cfg)
    daily_orchestrate(cfg, dry_run=False, immediate=immediate)


@main.command("dry-run")
@click.option("--now", "immediate", is_flag=True, default=True, help="Simulate commits now")
@click.pass_context
def dry_run_cmd(ctx: click.Context, immediate: bool) -> None:
    """Simulate today's job without committing or pushing."""
    cfg = _load(ctx.obj.get("config_path"))
    setup_logging(cfg.log_path, verbose=ctx.obj.get("verbose", False))
    daily_orchestrate(cfg, dry_run=True, immediate=immediate)
    click.echo("Dry-run finished (no commits pushed).")


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show this month's plan, skip days, and today's schedule."""
    cfg = _load(ctx.obj.get("config_path"))
    setup_logging(cfg.log_path, verbose=ctx.obj.get("verbose", False))
    snap = status_snapshot(cfg)
    click.echo(f"Timezone:  {snap['timezone']}")
    click.echo(f"Now:       {snap['now']}")
    click.echo(f"Paused:    {snap['paused']}")
    click.echo(f"Month:     {snap['month']}")
    click.echo(f"Skip days: {', '.join(snap['skip_days']) or '(none)'}")
    click.echo(f"Last run:  {snap['last_run'] or '(never)'}")

    today = snap["today"]
    if today:
        click.echo("\nToday:")
        click.echo(f"  date:            {today.date}")
        click.echo(f"  status:          {today.status}")
        click.echo(f"  scheduled_time:  {today.scheduled_time or '-'}")
        click.echo(f"  commits_planned: {today.commits_planned}")
        click.echo(f"  commits_made:    {today.commits_made}")
        if today.messages:
            click.echo(f"  messages:        {', '.join(today.messages)}")
        if today.error:
            click.echo(f"  error:           {today.error}")

    try:
        summary = git_ops.repo_status_summary(cfg)
        click.echo("\nRepo:")
        click.echo(f"  path:   {cfg.repo_path}")
        click.echo(f"  branch: {summary['branch']}")
        click.echo(f"  remote: {summary['remote']}")
        click.echo(f"  HEAD:   {summary['head']}")
    except Exception as exc:
        click.echo(f"\nRepo: unavailable ({exc})")

    click.echo(f"\nLog file: {cfg.log_path}")


@main.command()
@click.pass_context
def pause(ctx: click.Context) -> None:
    """Pause scheduling and commit jobs."""
    cfg = _load(ctx.obj.get("config_path"))
    setup_logging(cfg.log_path)
    set_paused(cfg, True)
    click.echo("Paused. Cron/service will no-op until resume.")


@main.command()
@click.pass_context
def resume(ctx: click.Context) -> None:
    """Resume after pause."""
    cfg = _load(ctx.obj.get("config_path"))
    setup_logging(cfg.log_path)
    set_paused(cfg, False)
    click.echo("Resumed.")


@main.command()
@click.confirmation_option(prompt="Remove gitcommit cron entry?")
@click.pass_context
def uninstall(ctx: click.Context) -> None:
    """Remove the cron entry installed by init."""
    try:
        removed = uninstall_cron()
    except Exception as exc:
        click.echo(f"Failed: {exc}", err=True)
        sys.exit(1)
    if removed:
        click.echo("Cron entry removed.")
    else:
        click.echo("No gitcommit cron entry found.")


@main.command()
@click.pass_context
def service(ctx: click.Context) -> None:
    """Run as a long-running background service (no cron needed)."""
    cfg = _load(ctx.obj.get("config_path"))
    Service(cfg).run()


@main.command("cloud-run")
@click.option(
    "--force",
    is_flag=True,
    help="Run even if scheduled time has not arrived yet",
)
def cloud_run_cmd(force: bool) -> None:
    """Cloud entrypoint for GitHub Actions (no local PC needed)."""
    from gitcommit.cloud import cloud_run

    sys.exit(cloud_run(force=force))


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8765, show_default=True, type=int)
@click.pass_context
def dashboard(ctx: click.Context, host: str, port: int) -> None:
    """Start a local web dashboard (calendar + log viewer)."""
    cfg = _load(ctx.obj.get("config_path"))
    setup_logging(cfg.log_path, verbose=ctx.obj.get("verbose", False))
    try:
        import uvicorn
        from gitcommit.dashboard.app import create_app
    except ImportError as exc:
        click.echo(f"Dashboard deps missing: {exc}. pip install -r requirements.txt", err=True)
        sys.exit(1)

    app = create_app(cfg)
    click.echo(f"Dashboard at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


@main.command("show-log")
@click.option("-n", "--lines", default=50, show_default=True)
@click.pass_context
def show_log(ctx: click.Context, lines: int) -> None:
    """Print the tail of the application log."""
    cfg = _load(ctx.obj.get("config_path"))
    for line in read_log_tail(cfg.log_path, lines):
        click.echo(line)


if __name__ == "__main__":
    main()
