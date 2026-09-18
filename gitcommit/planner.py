"""Monthly skip-day planning and daily random schedules."""

from __future__ import annotations

import calendar
import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from gitcommit.config import Config, window_minutes
from gitcommit.logger import get_logger
from gitcommit.state import AppState, DayRecord, MonthPlan, load_state, save_state


def _tz(cfg: Config) -> ZoneInfo:
    return ZoneInfo(cfg.timezone)


def now_local(cfg: Config) -> datetime:
    return datetime.now(_tz(cfg))


def _month_rng(year: int, month: int) -> random.Random:
    """Deterministic RNG so cloud runners always agree on the same skip days."""
    return random.Random(f"gitcommit-month:{year:04d}-{month:02d}")


def _day_rng(day: date) -> random.Random:
    return random.Random(f"gitcommit-day:{day.isoformat()}")


def ensure_month_plan(cfg: Config, when: date | None = None) -> MonthPlan:
    """Pick 3–4 skip days at month start and persist; stable mid-month."""
    log = get_logger()
    local_now = now_local(cfg)
    target = when or local_now.date()
    year, month = target.year, target.month

    state = load_state(cfg.state_path)
    existing = state.get_plan(year, month)
    if existing is not None:
        return existing

    days_in_month = calendar.monthrange(year, month)[1]
    all_days = [date(year, month, d) for d in range(1, days_in_month + 1)]

    rng = _month_rng(year, month)
    skip_count = rng.randint(cfg.skip_days_min, cfg.skip_days_max)
    skip_count = min(skip_count, len(all_days))
    skip_set = set(rng.sample(all_days, skip_count))
    skip_days = sorted(d.isoformat() for d in skip_set)

    plan = MonthPlan(year=year, month=month, skip_days=skip_days, days={})
    for d in all_days:
        key = d.isoformat()
        if d in skip_set:
            plan.days[key] = DayRecord(date=key, status="skipped")
        else:
            plan.days[key] = DayRecord(date=key, status="planned")

    state.set_plan(plan)
    save_state(cfg.state_path, state)
    log.info(
        "Created month plan %04d-%02d: skip days %s",
        year,
        month,
        ", ".join(skip_days),
    )
    return plan


def pick_random_time(cfg: Config, day: date) -> datetime:
    start_m, end_m = window_minutes(cfg)
    rng = _day_rng(day)
    minute = rng.randint(start_m, end_m - 1)
    hour, minute_of_hour = divmod(minute, 60)
    return datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute_of_hour,
        0,
        tzinfo=_tz(cfg),
    )


def pick_commit_count(cfg: Config, day: date) -> int:
    rng = _day_rng(day)
    # Separate stream from time pick
    rng.random()
    return rng.randint(cfg.min_commits, cfg.max_commits)


def schedule_today(cfg: Config, force_time: datetime | None = None) -> DayRecord:
    """Decide skip vs schedule for today; store scheduled_time and commit count."""
    log = get_logger()
    local = now_local(cfg)
    today = local.date()
    plan = ensure_month_plan(cfg, today)
    key = today.isoformat()
    rec = plan.days.get(key) or DayRecord(date=key, status="planned")

    if key in plan.skip_days or rec.status == "skipped":
        rec.status = "skipped"
        plan.days[key] = rec
        state = load_state(cfg.state_path)
        state.set_plan(plan)
        save_state(cfg.state_path, state)
        log.info("%s is a skip day — no commits", key)
        return rec

    if rec.status == "completed":
        log.info("%s already completed (%d commits)", key, rec.commits_made)
        return rec

    if rec.scheduled_time and rec.status in ("scheduled", "failed") and force_time is None:
        # Keep existing schedule unless forcing
        log.info(
            "%s already scheduled at %s (%d commits)",
            key,
            rec.scheduled_time,
            rec.commits_planned,
        )
        return rec

    when = force_time or pick_random_time(cfg, today)
    # If random time already passed and catch_up, schedule ASAP (+1–5 min)
    if when <= local and cfg.missed_schedule == "catch_up":
        delay = random.randint(1, 5)
        when = local + timedelta(minutes=delay)
        # Cap at window end
        _, end_m = window_minutes(cfg)
        end_dt = datetime.combine(
            today, time(end_m // 60, end_m % 60), tzinfo=_tz(cfg)
        )
        if when > end_dt:
            when = local + timedelta(minutes=1)
        log.info("Scheduled time had passed; catch-up at %s", when.strftime("%H:%M"))
    elif when <= local and cfg.missed_schedule == "skip":
        rec.status = "missed"
        plan.days[key] = rec
        state = load_state(cfg.state_path)
        state.set_plan(plan)
        save_state(cfg.state_path, state)
        log.info("%s scheduled time missed and missed_schedule=skip", key)
        return rec

    n = pick_commit_count(cfg, today)
    rec.status = "scheduled"
    rec.scheduled_time = when.strftime("%H:%M")
    rec.commits_planned = n
    rec.error = None
    plan.days[key] = rec
    state = load_state(cfg.state_path)
    state.set_plan(plan)
    save_state(cfg.state_path, state)
    log.info(
        "Scheduled %s at %s with %d commit(s)",
        key,
        rec.scheduled_time,
        n,
    )
    return rec


def scheduled_datetime(cfg: Config, rec: DayRecord) -> datetime | None:
    if not rec.scheduled_time:
        return None
    day = date.fromisoformat(rec.date)
    hour, minute = map(int, rec.scheduled_time.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=_tz(cfg))


def is_paused(cfg: Config) -> bool:
    if cfg.pause_flag.exists():
        return True
    state = load_state(cfg.state_path)
    return state.paused


def set_paused(cfg: Config, paused: bool) -> None:
    state = load_state(cfg.state_path)
    state.paused = paused
    save_state(cfg.state_path, state)
    if paused:
        cfg.pause_flag.write_text("paused\n", encoding="utf-8")
    elif cfg.pause_flag.exists():
        cfg.pause_flag.unlink()


def status_snapshot(cfg: Config) -> dict:
    local = now_local(cfg)
    plan = ensure_month_plan(cfg, local.date())
    today_key = local.date().isoformat()
    today = plan.days.get(today_key)
    state = load_state(cfg.state_path)
    return {
        "timezone": cfg.timezone,
        "now": local.isoformat(timespec="seconds"),
        "paused": is_paused(cfg),
        "month": plan.key(),
        "skip_days": plan.skip_days,
        "today": today,
        "last_run": state.last_run,
        "days": plan.days,
    }
