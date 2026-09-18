"""Execute commit jobs: spaced commits + push."""

from __future__ import annotations

import random
import time
from datetime import datetime

from gitcommit import git_ops
from gitcommit.config import Config
from gitcommit.logger import get_logger
from gitcommit.planner import ensure_month_plan, is_paused, now_local
from gitcommit.state import load_state, save_state, update_day


def _spacing_seconds(cfg: Config) -> int:
    lo = max(0, cfg.commit_spacing_min)
    hi = max(lo, cfg.commit_spacing_max)
    return random.randint(lo, hi) * 60


def run_commit_job(cfg: Config, dry_run: bool = False) -> bool:
    """
    Make today's planned commits and push.
    Returns True if commits were made (or would be in dry-run).
    """
    log = get_logger()
    if is_paused(cfg) and not dry_run:
        log.info("Bot is paused; skipping commit job")
        return False

    local = now_local(cfg)
    today = local.date()
    plan = ensure_month_plan(cfg, today)
    key = today.isoformat()
    rec = plan.days.get(key)

    if rec is None:
        rec = schedule_today(cfg)
        plan = ensure_month_plan(cfg, today)
        rec = plan.days[key]

    if key in plan.skip_days or (rec and rec.status == "skipped"):
        log.info("Skip day — no commits")
        return False

    if rec and rec.status == "completed" and not dry_run:
        log.info("Already completed today")
        return False

    if rec and rec.status == "missed" and cfg.missed_schedule == "skip":
        log.info("Day marked missed; not running")
        return False

    try:
        git_ops.ensure_repo(cfg)
    except Exception as exc:
        log.error("Repo setup failed: %s", exc)
        if not dry_run:
            update_day(
                cfg.state_path,
                today.year,
                today.month,
                today,
                status="failed",
                error=str(exc),
            )
        return False

    # If commits already landed but push failed earlier, only retry push
    push_only = bool(
        rec
        and rec.status == "failed"
        and rec.commits_made > 0
        and not dry_run
    )

    n = rec.commits_planned if rec and rec.commits_planned else random.randint(
        cfg.min_commits, cfg.max_commits
    )
    messages: list[str] = list(rec.messages) if push_only and rec else []
    errors: list[str] = []

    if not push_only:
        for i in range(n):
            msg = random.choice(cfg.commit_messages)
            when = now_local(cfg)
            try:
                git_ops.make_commit(cfg, msg, when, dry_run=dry_run)
                messages.append(msg)
                log.info("Commit %d/%d done", i + 1, n)
            except Exception as exc:
                log.error("Commit %d/%d failed: %s", i + 1, n, exc)
                errors.append(str(exc))
                break

            if i < n - 1:
                delay = _spacing_seconds(cfg)
                if dry_run:
                    log.info(
                        "[dry-run] Would wait %d seconds before next commit", delay
                    )
                else:
                    log.info("Waiting %d seconds before next commit", delay)
                    time.sleep(delay)
    else:
        log.info(
            "Retrying push only (%d commit(s) already made)",
            rec.commits_made if rec else 0,
        )

    if not messages:
        if not dry_run:
            update_day(
                cfg.state_path,
                today.year,
                today.month,
                today,
                status="failed",
                error="; ".join(errors) or "no commits made",
            )
        return False

    # Pull --rebase then push
    push_error = None
    if not dry_run:
        try:
            git_ops.pull_rebase(cfg)
            git_ops.push(cfg)
        except Exception as exc:
            push_error = str(exc)
            log.error("Push pipeline failed: %s", exc)
            try:
                log.info("Retrying pull --rebase + push")
                git_ops.pull_rebase(cfg)
                git_ops.push(cfg)
                push_error = None
                log.info("Retry succeeded")
            except Exception as exc2:
                push_error = str(exc2)
                log.error("Retry failed: %s", exc2)
    else:
        log.info("[dry-run] Would pull --rebase and push %d commit(s)", len(messages))

    if dry_run:
        return True

    status = "completed" if not push_error else "failed"
    update_day(
        cfg.state_path,
        today.year,
        today.month,
        today,
        status=status,
        commits_planned=n,
        commits_made=len(messages) if not push_only else (rec.commits_made if rec else len(messages)),
        messages=messages,
        error=push_error,
        completed_at=datetime.now().isoformat(timespec="seconds"),
    )
    state = load_state(cfg.state_path)
    state.last_run = datetime.now().isoformat(timespec="seconds")
    save_state(cfg.state_path, state)
    return status == "completed"


def sleep_until(target: datetime, cfg: Config) -> None:
    log = get_logger()
    while True:
        now = now_local(cfg)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        chunk = min(remaining, 60.0)
        log.debug("Sleeping %.0fs until %s", remaining, target.strftime("%H:%M:%S"))
        time.sleep(chunk)
