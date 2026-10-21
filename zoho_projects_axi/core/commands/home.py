"""`home`: what `zoho-projects-axi` prints with no arguments (AXI principle 8: live data, not a help dump).

With a default project: its name and status, how many tasks are open (split by status), how many are
overdue or due within a week, and the five most urgent of those. That is one or two requests cold (the
project record and one page of tasks) and none when warm. Without a default project it lists the
projects with a hint to `project use`. Without credentials it cannot show anything live, so it reports
the configuration offline and points at `auth login-help`.
"""
from __future__ import annotations

from datetime import timedelta

from ..result import Hint, Result
from ..status import auth_status
from . import _shape, project, tasks
from ._scope import optional, scope
from .base import Context

DUE_SOON_DAYS = 7
DUE_ROWS = 5


def _offline(ctx: Context, credentials: str) -> Result:
    payload = {
        "portal": ctx.settings.portal_id or "unset",
        "project": ctx.project or ctx.settings.project_id or "unset",
        "credentials": credentials,
        "note": "offline: credentials are missing, so no live data",
    }
    return Result("home", payload, help=[Hint("auth login-help"), Hint("auth status")])


def home(ctx: Context) -> Result:
    credentials = auth_status(ctx.settings, online=False)["credentials"]
    if credentials != "complete":
        return _offline(ctx, credentials)
    if not (ctx.project or ctx.settings.project_id):
        listing = project.list_projects(ctx)
        listing.warnings.insert(0, "no default project is set; pick one with `zoho-projects-axi project use <name>`")
        return listing

    portal, proj = scope(ctx)
    warnings: list[str] = []
    record = ctx.data.get("project", portal=portal, project=proj)
    page = optional(warnings, "tasks", lambda: tasks.fetch_page(ctx, portal, proj))

    payload: dict = {"project": proj, "name": str(record.get("name", "")), "status": _shape.status_name(record)}
    counts: dict = {}
    due_rows: list[dict] = []
    hints: list[Hint] = []
    if page is not None:
        open_tasks = [t for t in page if not _shape.is_closed(t)]
        today = ctx.current_date()
        soon_until = (today + timedelta(days=DUE_SOON_DAYS)).isoformat()
        dated = sorted(((_shape.due_date(t), _shape.task_row(t)) for t in open_tasks if _shape.due_date(t)),
                       key=lambda pair: pair[0])  # stable: equal dates keep Zoho's order
        overdue = [row for due, row in dated if due < today.isoformat()]
        soon = [row for due, row in dated if today.isoformat() <= due <= soon_until]
        counts = {"open_tasks": f"{len(open_tasks)}+" if page.has_more is True else len(open_tasks),
                  "overdue": len(overdue), "due_soon": len(soon)}
        counts.update(_shape.tally([_shape.status_name(t) for t in open_tasks]))
        due_rows = [*overdue, *soon][:DUE_ROWS]
        payload["due"] = due_rows
        if page.has_more is True:
            warnings.append(f"counts cover the first {len(page)} tasks only; `zoho-projects-axi tasks` pages through the rest")
        if due_rows:
            hints.append(Hint("task", {"ref": due_rows[0]["key"]}, positional=("ref",)))
    hints += [Hint("tasks"), Hint("project")]
    return Result("home", payload, counts=counts, warnings=warnings, help=hints)
