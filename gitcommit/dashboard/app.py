"""FastAPI dashboard for month calendar and logs."""

from __future__ import annotations

import calendar
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from gitcommit.config import Config
from gitcommit.logger import read_log_tail
from gitcommit.planner import ensure_month_plan, is_paused, now_local, status_snapshot


TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="GitCommit Bot", docs_url="/api/docs")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        snap = status_snapshot(cfg)
        local = now_local(cfg)
        plan = ensure_month_plan(cfg, local.date())
        cal = calendar.Calendar(firstweekday=0)
        weeks = []
        for week in cal.monthdatescalendar(plan.year, plan.month):
            row = []
            for d in week:
                key = d.isoformat()
                in_month = d.month == plan.month
                rec = plan.days.get(key) if in_month else None
                status = rec.status if rec else "other"
                row.append(
                    {
                        "date": d,
                        "day": d.day,
                        "in_month": in_month,
                        "status": status,
                        "scheduled_time": rec.scheduled_time if rec else None,
                        "commits_made": rec.commits_made if rec else 0,
                        "is_today": d == local.date(),
                    }
                )
            weeks.append(row)

        logs = read_log_tail(cfg.log_path, 80)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "month_label": f"{calendar.month_name[plan.month]} {plan.year}",
                "weeks": weeks,
                "skip_days": plan.skip_days,
                "paused": is_paused(cfg),
                "today": snap.get("today"),
                "logs": logs,
                "timezone": cfg.timezone,
                "repo": str(cfg.repo_path),
            },
        )

    @app.get("/api/status")
    def api_status() -> JSONResponse:
        snap = status_snapshot(cfg)
        today = snap.get("today")
        return JSONResponse(
            {
                "timezone": snap["timezone"],
                "now": snap["now"],
                "paused": snap["paused"],
                "month": snap["month"],
                "skip_days": snap["skip_days"],
                "last_run": snap["last_run"],
                "today": None
                if today is None
                else {
                    "date": today.date,
                    "status": today.status,
                    "scheduled_time": today.scheduled_time,
                    "commits_planned": today.commits_planned,
                    "commits_made": today.commits_made,
                    "messages": today.messages,
                    "error": today.error,
                },
            }
        )

    @app.get("/api/logs")
    def api_logs(n: int = 100) -> JSONResponse:
        return JSONResponse({"lines": read_log_tail(cfg.log_path, n)})

    return app
