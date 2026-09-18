"""Persistent month plan and daily run state."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass
class DayRecord:
    date: str  # YYYY-MM-DD
    status: str  # planned | skipped | scheduled | completed | missed | failed
    scheduled_time: str | None = None  # HH:MM local
    commits_planned: int = 0
    commits_made: int = 0
    messages: list[str] = field(default_factory=list)
    error: str | None = None
    completed_at: str | None = None


@dataclass
class MonthPlan:
    year: int
    month: int
    skip_days: list[str]  # YYYY-MM-DD
    days: dict[str, DayRecord] = field(default_factory=dict)

    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


@dataclass
class AppState:
    paused: bool = False
    current_month: str | None = None
    plans: dict[str, MonthPlan] = field(default_factory=dict)
    last_run: str | None = None
    service_pid: int | None = None

    def get_plan(self, year: int, month: int) -> MonthPlan | None:
        return self.plans.get(f"{year:04d}-{month:02d}")

    def set_plan(self, plan: MonthPlan) -> None:
        self.plans[plan.key()] = plan
        self.current_month = plan.key()


_lock = threading.Lock()


def _day_from_dict(data: dict[str, Any]) -> DayRecord:
    return DayRecord(
        date=data["date"],
        status=data.get("status", "planned"),
        scheduled_time=data.get("scheduled_time"),
        commits_planned=int(data.get("commits_planned", 0)),
        commits_made=int(data.get("commits_made", 0)),
        messages=list(data.get("messages") or []),
        error=data.get("error"),
        completed_at=data.get("completed_at"),
    )


def _plan_from_dict(data: dict[str, Any]) -> MonthPlan:
    days_raw = data.get("days") or {}
    days = {k: _day_from_dict(v) for k, v in days_raw.items()}
    return MonthPlan(
        year=int(data["year"]),
        month=int(data["month"]),
        skip_days=list(data.get("skip_days") or []),
        days=days,
    )


def load_state(path: Path) -> AppState:
    if not path.is_file():
        return AppState()
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    plans = {
        k: _plan_from_dict(v) for k, v in (raw.get("plans") or {}).items()
    }
    return AppState(
        paused=bool(raw.get("paused", False)),
        current_month=raw.get("current_month"),
        plans=plans,
        last_run=raw.get("last_run"),
        service_pid=raw.get("service_pid"),
    )


def save_state(path: Path, state: AppState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "paused": state.paused,
        "current_month": state.current_month,
        "last_run": state.last_run,
        "service_pid": state.service_pid,
        "plans": {
            key: {
                "year": plan.year,
                "month": plan.month,
                "skip_days": plan.skip_days,
                "days": {d: asdict(rec) for d, rec in plan.days.items()},
            }
            for key, plan in state.plans.items()
        },
    }
    tmp = path.with_suffix(".tmp")
    with _lock:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        tmp.replace(path)


def update_day(
    path: Path,
    year: int,
    month: int,
    day_date: date,
    **updates: Any,
) -> DayRecord:
    with _lock:
        state = load_state(path)
        plan = state.get_plan(year, month)
        if plan is None:
            raise KeyError(f"No plan for {year}-{month:02d}")
        key = day_date.isoformat()
        rec = plan.days.get(key) or DayRecord(date=key, status="planned")
        for field_name, value in updates.items():
            if hasattr(rec, field_name):
                setattr(rec, field_name, value)
        plan.days[key] = rec
        state.set_plan(plan)
        state.last_run = datetime.now().isoformat(timespec="seconds")
        # Persist without re-entering lock via nested save
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "paused": state.paused,
            "current_month": state.current_month,
            "last_run": state.last_run,
            "service_pid": state.service_pid,
            "plans": {
                k: {
                    "year": p.year,
                    "month": p.month,
                    "skip_days": p.skip_days,
                    "days": {d: asdict(r) for d, r in p.days.items()},
                }
                for k, p in state.plans.items()
            },
        }
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        tmp.replace(path)
        return rec
