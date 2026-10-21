"""`zoho-axi bug <KEY|ID> [--comments] [--attachments] [--task]`: one bug, lean by default (T09).

Same shape as `task` (T08): resolve the key (`_item`), fetch the detail, fetch only the slices the caller
asked for, map Zoho's records into display rows and hand a `Result` to the renderer.

Request budget (cold cache; a warm repeat inside the TTL costs 0):

    numeric id   bug + comments + attachments                      = 3
    key          the above + one Search call (then memoized)       = 4
    slices       --comments / --attachments reuse the default's calls (0 extra);
                 --task +1 (the linked task's detail; 0 when the bug links none)

The default fetches comments and attachments only for their counts: the bug detail carries no numbers.

Bug <-> task link (unverified). The legacy code reads `associated_task` off the bug detail, but Zoho's v3
docs do not show that field; they document a separate associated-tasks endpoint instead. This command reads
the field and, when `--task` finds it absent, says so in a warning rather than guessing an endpoint.

Bug field spellings differ between v3 and legacy (`name`/`title`, `severity.value`/`severity.type`,
`module.value`/`module.name`, `assignee`/`assignee_name`, `created_by`/`reported_person`), so every reader
is tolerant and shows "" when a field is missing.

Partial failure: a sub-fetch that fails becomes a `warnings` line and the command still exits 0.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..result import Hint, Result, Truncation
from ._item import (
    BUG, Item, attachment_rows, comment_rows, count_of, fetch_record, fetch_slice, image_rows, is_image, resolve_item,
)
from ._scope import optional, scope
from ._shape import due_date, norm_date, owner_names, person_name, status_name, task_key, task_row
from .base import Context

COMMENT_TAIL = 3     # comments shown by `--comments` unless --full
_NO_LINK = (
    "task: this bug has no `associated_task` field; Zoho's v3 docs expose the bug-task link through a separate "
    "associated-tasks endpoint that is not queried (unverified), so a link may exist"
)
# Raw record fields already shown (or deliberately hidden) so `--full` does not repeat them.
_MAPPED = {"name", "title", "prefix", "key", "description", "severity", "status", "assignee", "assignee_name",
           "created_by", "reported_person", "module", "created_time", "created_time_long", "due_date",
           "end_date", "associated_task", "owner", "owner_name"}


def bug(ctx: Context) -> Result:
    params = ctx.params
    portal, project = scope(ctx)
    item = resolve_item(ctx, BUG, params["ref"], portal, project)
    record = fetch_record(ctx, item)
    warnings: list[str] = []

    comments = fetch_slice(ctx, warnings, "comments", BUG.comments, item)
    attachments = fetch_slice(ctx, warnings, "attachments", BUG.attachments, item)

    link = _linked_task(record)
    payload = _fields(ctx, record, params["ref"], link)
    counts: dict[str, Any] = {}
    if (n := count_of(comments)) is not None:
        counts["comments"] = n
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
    if params.get("task"):
        _task_slice(ctx, item, link, payload, warnings)

    return Result("bug", payload, counts=counts, warnings=warnings, truncations=truncations,
                  help=_help(payload["key"], params, counts, comments, link, ctx.full))


# --- the detail fields -------------------------------------------------------------------------------

def _fields(ctx: Context, record: Mapping[str, Any], ref: str, link: Mapping[str, Any] | None) -> dict[str, Any]:
    key = task_key(record)
    if key == str(record.get("id", "")) and not ref.strip().isdigit():
        key = ref.strip()  # the record carried no key of its own: show the one the caller used
    payload: dict[str, Any] = {
        "key": key,
        "title": str(record.get("name") or record.get("title") or ""),
        "status": status_name(record),
        "severity": _label(record.get("severity")),
        "owner": ", ".join(owner_names(record)) or person_name(record.get("assignee_name")),
        "reporter": person_name(record.get("created_by")) or person_name(record.get("reported_person")),
        "module": _label(record.get("module")),
        "created": norm_date(record.get("created_time") or record.get("created_time_long")),
        "due": due_date(record),
        "task": task_key(link) if link else "",
        "description": str(record.get("description") or ""),
    }
    if ctx.full:  # every other plain field Zoho sent (nested objects stay out)
        for name, value in record.items():
            if name not in payload and name not in _MAPPED and isinstance(value, (str, int, float, bool)) \
                    and value != "":
                payload[name] = value
    return payload


def _label(value: Any) -> str:
    """A select-list value: v3 `{id, value}`, legacy `{id, type}` or `{id, name}`, or a bare string."""
    if isinstance(value, Mapping):
        return str(value.get("value") or value.get("name") or value.get("type") or "").strip()
    return str(value).strip() if value not in (None, "") else ""


def _linked_task(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """`associated_task` as a record with at least an id; None when absent, null or empty. Tolerates the
    object the legacy code expects, a one-element list, and a bare id."""
    value = record.get("associated_task")
    if isinstance(value, list):
        value = next((v for v in value if v), None)
    if isinstance(value, Mapping):
        return dict(value) if value.get("id") or value.get("prefix") or value.get("key") else None
    if value not in (None, "") and not isinstance(value, (list, bool)):
        return {"id": str(value)}
    return None


# --- the linked-task slice ---------------------------------------------------------------------------

def _task_slice(ctx: Context, item: Item, link: Mapping[str, Any] | None, payload: dict,
                warnings: list[str]) -> None:
    if link is None:
        warnings.append(_NO_LINK)
        return
    detail = None
    if link.get("id"):
        detail = optional(warnings, "linked task", lambda: ctx.data.get(
            "task", portal=item.portal, project=item.project, task=str(link["id"])))
    record = detail or link  # the embedded {id, name, prefix} is the fallback when the fetch failed
    payload["linked_task"] = [task_row(record)]


# --- next steps --------------------------------------------------------------------------------------

def _help(key: str, params: Mapping[str, Any], counts: Mapping[str, Any], comments: Sequence[Any] | None,
          link: Mapping[str, Any] | None, full: bool) -> list[Hint]:
    """Only slices that have something in them and were not already asked for."""
    def hint(**flags: bool) -> Hint:
        return Hint("bug", {"ref": key, **flags}, positional=("ref",))

    hints = []
    if counts.get("comments") not in (0, None) and not params.get("comments"):
        hints.append(hint(comments=True))
    elif params.get("comments") and comments is not None and len(comments) > COMMENT_TAIL and not full:
        hints.append(hint(comments=True, full=True))
    if counts.get("attachments") not in (0, None) and not params.get("attachments"):
        hints.append(hint(attachments=True))
    if params.get("attachments") and counts.get("images") and not params.get("images"):
        hints.append(hint(images=True))
    if link is not None and not params.get("task"):
        hints.append(hint(task=True))
    return hints
