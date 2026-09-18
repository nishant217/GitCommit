"""Long-running background service mode."""

from __future__ import annotations

import signal
import time
from datetime import datetime, timedelta

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
from gitcommit.scheduler import handle_startup_catchup
from gitcommit.state import load_state, save_state


class Service:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._stop = False

    def stop(self, *_args) -> None:
        get_logger().info("Service stop requested")
        self._stop = True

    def run(self) -> None:
        setup_logging(self.cfg.log_path)
        log = get_logger()
        log.info("Starting long-running service (timezone=%s)", self.cfg.timezone)

        state = load_state(self.cfg.state_path)
        state.service_pid = os_getpid()
        save_state(self.cfg.state_path, state)

        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        handle_startup_catchup(self.cfg)

        while not self._stop:
            if is_paused(self.cfg):
                log.info("Paused; sleeping 60s")
                self._sleep(60)
                continue

            local = now_local(self.cfg)
            ensure_month_plan(self.cfg, local.date())
            rec = schedule_today(self.cfg)

            if rec.status == "skipped":
                log.info("Skip day — sleeping until tomorrow 00:05")
                self._sleep_until_next_morning()
                continue

            if rec.status == "completed":
                log.info("Today done — sleeping until tomorrow 00:05")
                self._sleep_until_next_morning()
                continue

            if rec.status == "missed":
                self._sleep_until_next_morning()
                continue

            target = scheduled_datetime(self.cfg, rec)
            if target is None:
                run_commit_job(self.cfg)
                continue

            if target > local:
                log.info("Service waiting until %s", rec.scheduled_time)
                sleep_until(target, self.cfg)
                if self._stop or is_paused(self.cfg):
                    continue

            run_commit_job(self.cfg)
            if self._stop:
                break
            self._sleep_until_next_morning()

        state = load_state(self.cfg.state_path)
        state.service_pid = None
        save_state(self.cfg.state_path, state)
        log.info("Service stopped")

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while not self._stop and time.time() < end:
            time.sleep(min(1.0, end - time.time()))

    def _sleep_until_next_morning(self) -> None:
        local = now_local(self.cfg)
        next_day = (local + timedelta(days=1)).date()
        target = datetime(
            next_day.year,
            next_day.month,
            next_day.day,
            0,
            5,
            0,
            tzinfo=local.tzinfo,
        )
        get_logger().info("Sleeping until %s", target.isoformat())
        sleep_until(target, self.cfg)


def os_getpid() -> int:
    import os

    return os.getpid()
