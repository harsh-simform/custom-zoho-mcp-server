"""`zoho-axi task <KEY|ID> [--comments] [--history] [--subtasks] [--attachments]`: one task, lean by default.

This is the template for the other item commands (T09 `bug` copies its shape): resolve the key (`_item`),
fetch the detail, fetch only the slices the caller asked for, turn Zoho's records into display rows
(`_shape`, `_item`), and hand a `Result` to the renderer. The command never formats text.

Request budget (cold cache; a warm repeat inside the TTL costs 0):

    numeric id   task + comments + attachments                     = 3
    key          the above + one Search call (then memoized)       = 4
    slices       --comments / --attachments reuse the default's calls (0 extra);
                 --subtasks +1 (skipped when has_subtasks is false); --history +1 (one project-wide page)

The default fetches comments and attachments only for their counts, because the task detail carries
booleans (`association_info.has_comments`), not numbers. The history count is *not* in the default: Zoho's
status-history endpoint cannot be scoped to one task (quirk 2), so counting it would cost a project-wide
page on every call, and it may not even contain this task.

Partial failure: a sub-fetch that fails becomes a `warnings` line and the command still exits 0.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..result import Hint, Result, Truncation
from ._item import (
    TASK, Item, attachment_rows, comment_rows, count_of, fetch_record, fetch_slice, image_rows, is_image,
    resolve_item,
)
from ._scope import optional, scope
from ._shape import due_date, norm_date, owner_names, person_name, status_name, task_key, task_row, truthy
from .base import Context

COMMENT_TAIL = 3     # comments shown by `--comments` unless --full
HISTORY_TAIL = 10    # status changes shown by `--history` unless --full
_NOT_ON_THE_PAGE = (
    "history: task {id} is not on the page Zoho returned (its status-history endpoint cannot be scoped to one "
    "task and returns a single project-wide page){more}; showing none rather than other tasks' history"
)
_SUBTASKS_UNVERIFIED = (
    "subtasks unverified: Zoho cannot filter tasks by parent; {detail}"
)
# Raw record fields already shown (or deliberately hidden) so `--full` does not repeat them.
_MAPPED = {"name", "prefix", "key", "description", "priority", "owners_and_work", "details", "owner_name",
           "assignee", "status", "tasklist", "tasklist_id", "milestone", "tags", "end_date", "due_date",
           "association_info"}


def task(ctx: Context) -> Result:
    params = ctx.params
    portal, project = scope(ctx)
    item = resolve_item(ctx, TASK, params["ref"], portal, project)
    record = fetch_record(ctx, item)
    warnings: list[str] = []

    # The default calls: their results give the counts, and --comments / --attachments reuse them.
    comments = fetch_slice(ctx, warnings, "comments", TASK.comments, item)
    attachments = fetch_slice(ctx, warnings, "attachments", TASK.attachments, item)

    payload = _fields(ctx, item, record, params["ref"])
    counts: dict[str, Any] = {}
    if (n := count_of(comments)) is not None:
        counts["comments"] = n
    subtasks_flag = _has_subtasks(record)
    counts["subtasks"] = 0 if subtasks_flag is False else "yes" if subtasks_flag else "?"
    if (n := count_of(attachments)) is not None:
        counts["attachments"] = n
        if attachments and (images := sum(is_image(a) for a in attachments)):
            counts["images"] = images

    truncations: list[Truncation] = []
    if params.get("comments") and comments is not None:
        shown = list(comments) if ctx.full else list(comments)[-COMMENT_TAIL:]
        payload["comments"] = comment_rows(shown)
        if len(shown) < len(comments):
            truncations.append(Truncation("comments", len(comments) - len(shown), "older", {"full": True}))
    if params.get("attachments") and attachments is not None:
        payload["attachments"] = attachment_rows(attachments)
    if params.get("images") and attachments is not None:  # implies the attachments fetch (already made above)
        payload["images"] = image_rows(ctx, item, attachments, warnings)
    if params.get("subtasks"):
        _subtasks(ctx, item, subtasks_flag, payload, counts, warnings)
    if params.get("history"):
        _history(ctx, item, payload, counts, truncations, warnings)

    display = payload["key"]
    return Result("task", payload, counts=counts, warnings=warnings, truncations=truncations,
                  help=_help(display, params, counts, comments, ctx.full))


# --- the detail fields -------------------------------------------------------------------------------

def _fields(ctx: Context, item: Item, record: Mapping[str, Any], ref: str) -> dict[str, Any]:
    index = ctx.data.index(portal=item.portal, project=item.project)
    key = task_key(record)
    if key == item.id and not ref.strip().isdigit():
        key = ref.strip()  # the record carried no key of its own: show the one the caller used
    payload: dict[str, Any] = {
        "key": key,
        "title": str(record.get("name") or record.get("title") or ""),
        "status": status_name(record),
        "owner": ", ".join(owner_names(record)),
        "priority": str(record.get("priority") or ""),
        "due": due_date(record),
        "tasklist": _named(record.get("tasklist") or record.get("tasklist_id"), "tasklist", index),
        "milestone": _named(record.get("milestone"), "milestone", index),
        "tags": ", ".join(t for t in (_named(t, "tag", index) for t in record.get("tags") or []) if t),
        "description": str(record.get("description") or ""),
    }
    if ctx.full:  # every other plain field Zoho sent (nested objects stay out)
        for name, value in record.items():
            if name not in payload and name not in _MAPPED and isinstance(value, (str, int, float, bool)) \
                    and value != "":
                payload[name] = value
    return payload


def _named(value: Any, kind: str, index) -> str:
    """A referenced object's name: from the record itself, else the warm index, else its id."""
    if isinstance(value, Mapping):
        return person_name(value) or index.name_of(kind, str(value.get("id", ""))) or str(value.get("id", ""))
    if value in (None, ""):
        return ""
    return index.name_of(kind, str(value)) or str(value)


def _has_subtasks(record: Mapping[str, Any]) -> bool | None:
    """`association_info.has_subtasks` as a bool; None when Zoho did not say (the field is not on every
    response). Zoho sometimes sends booleans as the strings "true"/"false"."""
    flag = (record.get("association_info") or {}).get("has_subtasks")
    if flag is None:
        return None
    return truthy(flag)


# --- slices ------------------------------------------------------------------------------------------

def _subtasks(ctx: Context, item: Item, flag: bool | None, payload: dict, counts: dict, warnings: list[str]) -> None:
    if flag is False:  # Zoho says there are none: the one case worth trusting (quirk 3), and it saves a call
        payload["subtasks"] = []
        return
    page = optional(warnings, "subtasks", lambda: ctx.data.get(
        "tasks", params={"parent_id": item.id}, portal=item.portal, project=item.project))
    if page is None:
        return
    linked = [t for t in page if (t.get("parental_info") or {}).get("parent_task_id") == item.id]
    if any(t.get("parental_info") for t in page):
        detail = "listing the tasks whose parent is this one (first page of the project only)"
    else:
        detail = "the page named no parents, so none can be listed; use `zoho-axi tasks`"
    warnings.append(_SUBTASKS_UNVERIFIED.format(detail=detail))
    payload["subtasks"] = [task_row(t) for t in linked]
    counts["subtasks"] = len(linked)


def _history(ctx: Context, item: Item, payload: dict, counts: dict, truncations: list[Truncation],
             warnings: list[str]) -> None:
    page = optional(warnings, "history", lambda: ctx.data.get(
        "task_status_history", portal=item.portal, project=item.project))
    if page is None:
        return
    mine = [e for e in page if str(e.get("id")) == item.id]
    if not mine:
        more = "; more pages exist and were not fetched" if getattr(page, "has_more", None) is True else ""
        warnings.append(_NOT_ON_THE_PAGE.format(id=item.id, more=more))
        return
    changes = sorted((c for e in mine for c in e.get("status_history") or []),
                     key=lambda c: str(c.get("updated_on", "")))
    rows = [{
        "at": norm_date(c.get("updated_on")),
        "by": person_name(c.get("updated_by")),
        "from": status_name({"status": c.get("previous_status")}),
        "to": status_name({"status": c.get("updated_status")}),
    } for c in changes]
    counts["history"] = len(rows)
    shown = rows if ctx.full else rows[-HISTORY_TAIL:]
    if len(shown) < len(rows):
        truncations.append(Truncation("history", len(rows) - len(shown), "older", {"full": True}))
    payload["history"] = shown


# --- next steps --------------------------------------------------------------------------------------

def _help(key: str, params: Mapping[str, Any], counts: Mapping[str, Any], comments: Sequence[Any] | None,
          full: bool) -> list[Hint]:
    """Only slices that have something in them and were not already asked for. History is never suggested
    from here: its count is unknown without a project-wide fetch (see the module docstring)."""
    def hint(**flags: bool) -> Hint:
        return Hint("task", {"ref": key, **flags}, positional=("ref",))

    hints = []
    if counts.get("comments") not in (0, None) and not params.get("comments"):
        hints.append(hint(comments=True))
    elif params.get("comments") and comments is not None and len(comments) > COMMENT_TAIL and not full:
        hints.append(hint(comments=True, full=True))
    if counts.get("subtasks") not in (0, None) and not params.get("subtasks"):
        hints.append(hint(subtasks=True))
    if counts.get("attachments") not in (0, None) and not params.get("attachments"):
        hints.append(hint(attachments=True))
    if params.get("attachments") and counts.get("images") and not params.get("images"):
        hints.append(hint(images=True))
    return hints
