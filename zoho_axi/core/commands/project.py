"""`zoho-axi project`, `project list` and `project use`.

`project` is a lean overview of one project: name, status, owner, dates, the next milestone due and
counts (tasklists, milestones, users, tags, open/closed tasks); full lists live behind `--lists` and
`people`. Every sub-fetch except the project record itself is optional: a milestones request that Zoho
refuses (v3 may call them `phases`) becomes a warning and the rest still renders.

`project use <name|id>` finds the project in the (cached) project list, or by a direct lookup for an id
the list does not show, and writes portal_id / project_id into the config file (see config_edit.py).
It replaces the old stateful select/switch tools: there is no other place a default is remembered.
"""
from __future__ import annotations

from ..config import split_project_ref
from ..config_edit import set_default_project
from ..errors import NotFoundError, UsageError
from ..result import Hint, Result
from . import _shape
from ._scope import names, optional, pick, project_not_found, scope
from .base import Context


def _project_row(record: dict, portal: str) -> dict:
    return {"id": str(record.get("id", "")), "name": str(record.get("name", "")),
            "status": _shape.status_name(record) or str(record.get("project_type", "")), "portal": portal}


def _next_milestone(milestones: list) -> str:
    open_ones = [(_shape.due_date(m), str(m.get("name", ""))) for m in milestones if not _shape.is_closed(m)]
    dated = sorted(item for item in open_ones if item[0])
    return f"{dated[0][1]} ({dated[0][0]})" if dated else ""


def overview(ctx: Context) -> Result:
    portal, project = scope(ctx)
    warnings: list[str] = []
    record = ctx.data.get("project", portal=portal, project=project)
    tasklists = optional(warnings, "tasklists", lambda: ctx.data.get("tasklists", portal=portal, project=project))
    milestones = optional(warnings, "milestones", lambda: ctx.data.get("milestones", portal=portal, project=project))
    users = optional(warnings, "users", lambda: ctx.data.get("users", portal=portal))
    tags = optional(warnings, "tags", lambda: ctx.data.get("tags", portal=portal))

    payload: dict = {
        "id": project,
        "name": str(record.get("name", "")),
        "status": _shape.status_name(record),
        "owner": ", ".join(_shape.owner_names(record)),
        "start": _shape.norm_date(record.get("start_date")),
        "end": _shape.norm_date(record.get("end_date")),
        "next_milestone": _next_milestone(milestones) if milestones else "",
        "description": str(record.get("description") or ""),  # only shown by --full
    }
    counts: dict = {}
    for label, found in (("tasklists", tasklists), ("milestones", milestones), ("users", users), ("tags", tags)):
        if found is not None:
            counts[label] = f"{len(found)}+" if found.has_more is True else len(found)
    for label, key in (("open_tasks", "open_count"), ("closed_tasks", "closed_count")):
        value = (record.get("tasks") or {}).get(key) if isinstance(record.get("tasks"), dict) else None
        if value is not None:
            counts[label] = value

    if ctx.params.get("lists"):
        if tasklists is not None:
            payload["tasklists"] = [{"id": str(t.get("id", "")), "name": str(t.get("name", "")),
                                     "status": _shape.status_name(t)} for t in tasklists]
        if milestones is not None:
            payload["milestones"] = [{"id": str(m.get("id", "")), "name": str(m.get("name", "")),
                                      "due": _shape.due_date(m), "status": _shape.status_name(m)} for m in milestones]
    hints = [Hint("tasks"), Hint("people")]
    if not ctx.params.get("lists"):
        hints.append(Hint("project", {"lists": True}))
    return Result("project", payload, counts=counts, warnings=warnings, help=hints)


def list_projects(ctx: Context) -> Result:
    portal = (split_project_ref(ctx.project)[0] if ctx.project else None) or ctx.settings.portal_id
    portals = [portal] if portal else [str(p["id"]) for p in ctx.data.get("portals") if p.get("id")]
    rows: list[dict] = []
    warnings: list[str] = []
    for pid in portals:
        found = ctx.data.get("projects", portal=pid)
        rows += [_project_row(r, pid) for r in found]
        if found.has_more is True:
            warnings.append(f"portal {pid} has more projects than one page; showing the first {len(found)}")
    hints = [Hint("project use", {"ref": rows[0]["name"] or rows[0]["id"]}, positional=("ref",))] if rows else []
    return Result("project", rows, counts={"projects": len(rows)}, warnings=warnings, help=hints)


def _use_portal(ctx: Context, ref: str) -> tuple[str, str]:
    portal, ref = split_project_ref(ref)
    if portal:
        return portal, ref
    if ctx.settings.portal_id:
        return ctx.settings.portal_id, ref
    portals = ctx.data.get("portals")
    if len(portals) == 1 and portals[0].get("id"):
        return str(portals[0]["id"]), ref
    raise UsageError("usage.portal_required", "More than one portal is available, or none could be read.",
                     hint=f"Use `zoho-axi project use PORTAL:{ref}` with one of: {names(portals) or 'no portals found'}.")


def use(ctx: Context) -> Result:
    ref = str(ctx.params["ref"]).strip()
    portal, ref = _use_portal(ctx, ref)
    record, candidates = pick(ctx.data.get("projects", portal=portal), ref)
    if record is None and not candidates and ref.isdigit():
        record = ctx.data.get("project", portal=portal, project=ref)  # an id the list page did not show
        if not record.get("id"):
            raise NotFoundError("not_found", f"project {ref}: not found", hint="Run `zoho-axi project list`.")
    if record is None:
        raise project_not_found(ref, candidates)
    project_id = str(record.get("id") or ref)

    set_default_project(ctx.settings.config_path, portal, project_id)
    warnings = []
    for name, env_name in (("portal_id", "ZOHO_AXI_PORTAL_ID"), ("project_id", "ZOHO_AXI_PROJECT_ID")):
        if ctx.settings.sources.get(name) == "env":
            warnings.append(f"{env_name} is set in the environment and still overrides the config file")
    payload = {"project": project_id, "portal": portal, "name": str(record.get("name", "")),
               "config": str(ctx.settings.config_path)}
    return Result("default", payload, warnings=warnings, help=[Hint("project"), Hint("tasks")])
