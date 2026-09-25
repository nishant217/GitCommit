"""Cloud (GitHub Actions) entrypoint — no local machine required."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from gitcommit.config import Config, load_config, save_config
from gitcommit.logger import get_logger, setup_logging
from gitcommit.planner import (
    is_paused,
    now_local,
    schedule_today,
    scheduled_datetime,
)
from gitcommit.runner import run_commit_job
from gitcommit.state import load_state, save_state


def build_cloud_config(config_path: str | Path | None = None) -> Config:
    """Load cloud config; force repo=cwd and data under .gitcommit-data/."""
    root = Path.cwd().resolve()
    data_dir = root / ".gitcommit-data"
    data_dir.mkdir(parents=True, exist_ok=True)

    env_config = os.environ.get("GITCOMMIT_CONFIG")
    path = config_path or env_config
    if path:
        cfg = load_config(path)
    else:
        cloud_yaml = root / "config.cloud.yaml"
        local_yaml = root / "config.yaml"
        if cloud_yaml.is_file():
            cfg = load_config(cloud_yaml)
        elif local_yaml.is_file():
            cfg = load_config(local_yaml)
        else:
            cfg = Config(
                repo_path=root,
                data_dir=data_dir,
                timezone=os.environ.get("GITCOMMIT_TIMEZONE", "Asia/Kolkata"),
                author_name=os.environ.get("GITCOMMIT_AUTHOR_NAME", ""),
                author_email=os.environ.get("GITCOMMIT_AUTHOR_EMAIL", ""),
                branch=os.environ.get("GITCOMMIT_BRANCH", "main"),
                missed_schedule="catch_up",
            )

    # Always operate on the checked-out repo in CI
    cfg.repo_path = root
    cfg.data_dir = data_dir
    cfg.missed_schedule = "catch_up"

    # Secrets / env override author (required for green squares)
    if os.environ.get("GITCOMMIT_AUTHOR_NAME"):
        cfg.author_name = os.environ["GITCOMMIT_AUTHOR_NAME"]
    if os.environ.get("GITCOMMIT_AUTHOR_EMAIL"):
        cfg.author_email = os.environ["GITCOMMIT_AUTHOR_EMAIL"]
    if os.environ.get("GITCOMMIT_TIMEZONE"):
        cfg.timezone = os.environ["GITCOMMIT_TIMEZONE"]
    if os.environ.get("GITCOMMIT_BRANCH"):
        cfg.branch = os.environ["GITCOMMIT_BRANCH"]

    if not cfg.author_name or not cfg.author_email:
        raise SystemExit(
            "Set GITCOMMIT_AUTHOR_NAME and GITCOMMIT_AUTHOR_EMAIL "
            "(GitHub Actions secrets) so commits count on your graph."
        )

    # Persist a copy for status/debug inside the repo data dir
    if not (data_dir / "config.yaml").exists():
        save_config(cfg, data_dir / "config.yaml")

    return cfg


def cloud_run(*, force: bool = False) -> int:
    """
    Designed for hourly GitHub Actions triggers:
    - Builds/loads month plan (stable skip days)
    - Skips if pause / skip-day / already completed
    - Waits until today's random time has arrived (next hourly tick runs it)
    - Then commits + pushes using GITHUB_TOKEN / GH_PAT
    """
    cfg = build_cloud_config()
    setup_logging(cfg.log_path, verbose=True)
    log = get_logger()
    log.info("=== Cloud run start (force=%s) ===", force)

    if is_paused(cfg) and not force:
        log.info("Paused (delete .gitcommit-data/PAUSED or unset paused in state)")
        return 0

    local = now_local(cfg)
    rec = schedule_today(cfg)

    if rec.status == "skipped":
        log.info("Skip day — nothing to do")
        return 0
    if rec.status == "completed":
        log.info("Already completed today")
        return 0
    if rec.status == "missed":
        log.info("Missed day")
        return 0

    target = scheduled_datetime(cfg, rec)
    if target and local < target and not force:
        log.info(
            "Too early (now %s, scheduled %s) — waiting for next Actions tick",
            local.strftime("%H:%M"),
            rec.scheduled_time,
        )
        # Persist schedule so later ticks see the same plan
        return 0

    ok = run_commit_job(cfg, dry_run=False)
    # Also commit state file into the repo so skip-days survive across runners
    _commit_state_sidecar(cfg)
    return 0 if ok else 1


def _commit_state_sidecar(cfg: Config) -> None:
    """Best-effort: ensure .gitcommit-data/state.json is tracked after a run."""
    log = get_logger()
    state_rel = ".gitcommit-data/state.json"
    state_path = cfg.repo_path / state_rel
    if not state_path.is_file():
        return
    try:
        from gitcommit import git_ops

        env = {
            "GIT_AUTHOR_NAME": cfg.author_name,
            "GIT_AUTHOR_EMAIL": cfg.author_email,
            "GIT_COMMITTER_NAME": cfg.author_name,
            "GIT_COMMITTER_EMAIL": cfg.author_email,
        }
        # Persist state only; runtime logs must remain ignored.
        git_ops._run(
            ["git", "add", "-f", "--", state_rel],
            cwd=cfg.repo_path,
            env=env,
        )
        status = git_ops._run(
            ["git", "diff", "--cached", "--name-only", "--", state_rel],
            cwd=cfg.repo_path,
        )
        if not (status.stdout or "").strip():
            return
        git_ops._run(
            ["git", "commit", "--only", "-m", "chore: sync gitcommit state", "--", state_rel],
            cwd=cfg.repo_path,
            env=env,
        )
        try:
            git_ops.push(cfg)
        except Exception as exc:
            log.warning("State sidecar push skipped: %s", exc)
    except Exception as exc:
        log.warning("Could not sync state sidecar: %s", exc)
