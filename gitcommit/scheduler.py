"""Daily orchestration: morning planner + delayed commit job."""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from gitcommit.config import Config
from gitcommit.logger import get_logger, setup_logging
from gitcommit.planner import (
    ensure_month_plan,
    is_paused,
    now_local,
    schedule_today,
    scheduled_datetime,
)
from gitcommit.runner import run_commit_job, sleep_until


CRON_MARKER = "# gitcommit-bot"


def daily_orchestrate(cfg: Config, dry_run: bool = False, immediate: bool = False) -> None:
    """
    Entry point for cron / service tick:
    - Ensure month plan
    - If skip day: exit
    - Else schedule random time and wait (or use `at`), then run commits
    """
    setup_logging(cfg.log_path)
    log = get_logger()
    log.info("=== Daily orchestrate start (dry_run=%s, immediate=%s) ===", dry_run, immediate)

    if is_paused(cfg):
        log.info("Paused — exiting")
        return

    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    local = now_local(cfg)
    ensure_month_plan(cfg, local.date())
    rec = schedule_today(cfg)

    if rec.status == "skipped":
        return
    if rec.status == "completed":
        log.info("Already done today")
        return
    if rec.status == "missed":
        log.info("Missed day (skip policy)")
        return

    if immediate:
        run_commit_job(cfg, dry_run=dry_run)
        return

    target = scheduled_datetime(cfg, rec)
    if target is None:
        log.error("No scheduled time; running immediately")
        run_commit_job(cfg, dry_run=dry_run)
        return

    # Prefer `at` if available and not dry-run; else sleep in-process
    if not dry_run and _try_schedule_at(cfg, target):
        log.info("Deferred commit job via `at` for %s", rec.scheduled_time)
        return

    log.info("Waiting until %s to run commits", rec.scheduled_time)
    sleep_until(target, cfg)
    # Re-check pause / skip after wake
    if is_paused(cfg):
        log.info("Paused during wait — aborting")
        return
    run_commit_job(cfg, dry_run=dry_run)


def _try_schedule_at(cfg: Config, target: datetime) -> bool:
    """Schedule a one-shot `at` job. Returns True if queued successfully."""
    log = get_logger()
    if not shutil.which("at"):
        log.debug("`at` not found; falling back to sleep")
        return False

    # `at` uses local system time; warn if TZ mismatch is likely
    at_time = target.strftime("%H:%M %Y-%m-%d")
    python = sys.executable
    config_arg = str(cfg.config_path) if cfg.config_path else ""
    cmd = (
        f'cd {shlex_quote(str(Path.cwd()))} && '
        f'{shlex_quote(python)} -m gitcommit run --now '
        f'--config {shlex_quote(config_arg)}'
    )
    # Prefer invoking via PATH entry if installed
    try:
        proc = subprocess.run(
            ["at", at_time],
            input=cmd + "\n",
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            log.warning("`at` failed: %s", (proc.stderr or proc.stdout or "").strip())
            return False
        log.info("`at` accepted job for %s", at_time)
        return True
    except OSError as exc:
        log.warning("Could not invoke `at`: %s", exc)
        return False


def shlex_quote(s: str) -> str:
    import shlex

    return shlex.quote(s)


def install_cron(cfg: Config, hour: int = 0, minute: int = 5) -> str:
    """Install a single daily cron entry. Returns the cron line."""
    log = get_logger()
    python = sys.executable
    # Ensure PYTHONPATH includes project root when running from source
    project_root = Path(__file__).resolve().parent.parent
    config_path = cfg.config_path or (cfg.data_dir / "config.yaml")

    env_prefix = (
        f"GITCOMMIT_CONFIG={shlex_quote(str(config_path))} "
        f"PYTHONPATH={shlex_quote(str(project_root))}"
    )
    line = (
        f"{minute} {hour} * * * {env_prefix} "
        f"{shlex_quote(python)} -m gitcommit run >> "
        f"{shlex_quote(str(cfg.log_path))} 2>&1 {CRON_MARKER}"
    )

    existing = subprocess.run(
        ["crontab", "-l"],
        capture_output=True,
        text=True,
        check=False,
    )
    old = existing.stdout if existing.returncode == 0 else ""
    # Remove previous gitcommit lines
    kept = [
        ln
        for ln in old.splitlines()
        if CRON_MARKER not in ln and ln.strip()
    ]
    kept.append(line)
    new_crontab = "\n".join(kept) + "\n"
    proc = subprocess.run(
        ["crontab", "-"],
        input=new_crontab,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"crontab install failed: {proc.stderr}")
    log.info("Installed cron: %s", line)
    # Also write helper script for Windows / systemd docs
    (cfg.data_dir / "cron.line").write_text(line + "\n", encoding="utf-8")
    return line


def uninstall_cron() -> bool:
    existing = subprocess.run(
        ["crontab", "-l"],
        capture_output=True,
        text=True,
        check=False,
    )
    if existing.returncode != 0:
        return False
    lines = existing.stdout.splitlines()
    kept = [ln for ln in lines if CRON_MARKER not in ln]
    if len(kept) == len(lines):
        return False
    new_crontab = "\n".join(kept) + ("\n" if kept else "")
    proc = subprocess.run(
        ["crontab", "-"],
        input=new_crontab,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"crontab uninstall failed: {proc.stderr}")
    return True


def handle_startup_catchup(cfg: Config) -> None:
    """If missed_schedule=catch_up and today is active but not completed, run now."""
    if cfg.missed_schedule != "catch_up":
        return
    if is_paused(cfg):
        return
    local = now_local(cfg)
    plan = ensure_month_plan(cfg, local.date())
    key = local.date().isoformat()
    rec = plan.days.get(key)
    if not rec:
        return
    if rec.status in ("completed", "skipped", "missed"):
        return
    # If scheduled time passed or never ran
    target = scheduled_datetime(cfg, rec)
    if target and target > local:
        return  # still waiting
    log = get_logger()
    log.info("Catch-up: running missed/pending job for %s", key)
    # Ensure we have a plan count
    if rec.status == "planned" or not rec.commits_planned:
        schedule_today(cfg)
    run_commit_job(cfg, dry_run=False)
