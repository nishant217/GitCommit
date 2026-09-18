"""Configuration loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

DEFAULT_DATA_DIR = Path.home() / ".gitcommit-bot"
DEFAULT_CONFIG_NAMES = ("config.yaml", "config.yml")


@dataclass
class Config:
    repo_path: Path
    branch: str = "main"
    author_name: str = ""
    author_email: str = ""
    timezone: str = "UTC"
    commit_window_start: str = "09:00"
    commit_window_end: str = "23:00"
    min_commits: int = 1
    max_commits: int = 5
    commit_spacing_min: int = 2
    commit_spacing_max: int = 8
    skip_days_min: int = 3
    skip_days_max: int = 4
    journal_file: str = "activity.log"
    missed_schedule: str = "catch_up"  # skip | catch_up
    remote_url: str = ""
    commit_messages: list[str] = field(default_factory=list)
    data_dir: Path = field(default_factory=lambda: DEFAULT_DATA_DIR)
    config_path: Path | None = None

    def __post_init__(self) -> None:
        if isinstance(self.repo_path, str):
            self.repo_path = Path(os.path.expanduser(self.repo_path)).resolve()
        if isinstance(self.data_dir, str):
            self.data_dir = (
                Path(os.path.expanduser(self.data_dir)).resolve()
                if self.data_dir
                else DEFAULT_DATA_DIR
            )
        if not self.commit_messages:
            self.commit_messages = [
                "Update activity log",
                "Add daily notes",
                "Record progress",
                "Sync journal entry",
            ]
        if self.missed_schedule not in ("skip", "catch_up"):
            raise ValueError("missed_schedule must be 'skip' or 'catch_up'")
        if self.min_commits < 1 or self.max_commits < self.min_commits:
            raise ValueError("Invalid min_commits/max_commits range")
        if self.skip_days_min < 0 or self.skip_days_max < self.skip_days_min:
            raise ValueError("Invalid skip_days range")

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "gitcommit.log"

    @property
    def pause_flag(self) -> Path:
        return self.data_dir / "PAUSED"

    @property
    def github_token(self) -> str | None:
        return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def default_config_path(data_dir: Path | None = None) -> Path:
    base = data_dir or DEFAULT_DATA_DIR
    return base / "config.yaml"


def find_config_path(explicit: str | Path | None = None) -> Path:
    if explicit:
        path = Path(os.path.expanduser(str(explicit))).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Config not found: {path}")
        return path

    env = os.environ.get("GITCOMMIT_CONFIG")
    if env:
        path = Path(os.path.expanduser(env)).resolve()
        if path.is_file():
            return path

    for name in DEFAULT_CONFIG_NAMES:
        cwd = Path.cwd() / name
        if cwd.is_file():
            return cwd.resolve()

    for name in DEFAULT_CONFIG_NAMES:
        home = DEFAULT_DATA_DIR / name
        if home.is_file():
            return home.resolve()

    raise FileNotFoundError(
        "No config found. Run `gitcommit init` or pass --config."
    )


def _parse_time(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time '{value}', expected HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time '{value}'")
    return hour, minute


def load_config(path: str | Path | None = None) -> Config:
    load_dotenv()
    config_path = find_config_path(path)
    with open(config_path, encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    data_dir_raw = raw.get("data_dir") or ""
    data_dir = (
        Path(os.path.expanduser(data_dir_raw)).resolve()
        if data_dir_raw
        else DEFAULT_DATA_DIR
    )

    cfg = Config(
        repo_path=Path(os.path.expanduser(raw.get("repo_path", "~/git-activity"))),
        branch=str(raw.get("branch", "main")),
        author_name=str(raw.get("author_name", "")),
        author_email=str(raw.get("author_email", "")),
        timezone=str(raw.get("timezone", "UTC")),
        commit_window_start=str(raw.get("commit_window_start", "09:00")),
        commit_window_end=str(raw.get("commit_window_end", "23:00")),
        min_commits=int(raw.get("min_commits", 1)),
        max_commits=int(raw.get("max_commits", 5)),
        commit_spacing_min=int(raw.get("commit_spacing_min", 2)),
        commit_spacing_max=int(raw.get("commit_spacing_max", 8)),
        skip_days_min=int(raw.get("skip_days_min", 3)),
        skip_days_max=int(raw.get("skip_days_max", 4)),
        journal_file=str(raw.get("journal_file", "activity.log")),
        missed_schedule=str(raw.get("missed_schedule", "catch_up")),
        remote_url=str(raw.get("remote_url", "") or ""),
        commit_messages=list(raw.get("commit_messages") or []),
        data_dir=data_dir,
        config_path=config_path,
    )
    # Validate times early
    _parse_time(cfg.commit_window_start)
    _parse_time(cfg.commit_window_end)
    return cfg


def save_config(cfg: Config, path: Path | None = None) -> Path:
    dest = path or cfg.config_path or default_config_path(cfg.data_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "repo_path": str(cfg.repo_path),
        "branch": cfg.branch,
        "author_name": cfg.author_name,
        "author_email": cfg.author_email,
        "timezone": cfg.timezone,
        "commit_window_start": cfg.commit_window_start,
        "commit_window_end": cfg.commit_window_end,
        "min_commits": cfg.min_commits,
        "max_commits": cfg.max_commits,
        "commit_spacing_min": cfg.commit_spacing_min,
        "commit_spacing_max": cfg.commit_spacing_max,
        "skip_days_min": cfg.skip_days_min,
        "skip_days_max": cfg.skip_days_max,
        "journal_file": cfg.journal_file,
        "missed_schedule": cfg.missed_schedule,
        "remote_url": cfg.remote_url,
        "commit_messages": cfg.commit_messages,
        "data_dir": str(cfg.data_dir),
    }
    with open(dest, "w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, default_flow_style=False, sort_keys=False)
    cfg.config_path = dest
    return dest


def window_minutes(cfg: Config) -> tuple[int, int]:
    """Return (start_minute_of_day, end_minute_of_day)."""
    sh, sm = _parse_time(cfg.commit_window_start)
    eh, em = _parse_time(cfg.commit_window_end)
    start = sh * 60 + sm
    end = eh * 60 + em
    if end <= start:
        raise ValueError("commit_window_end must be after commit_window_start")
    return start, end
