"""`zoho-projects-axi tasks`: a lean list of a project's tasks (key, title, status, owner) with totals by status.

`--tasklist` / `--milestone` (id or name) go to Zoho as the server-side `filter` criterion the legacy
server proved (`tasklist_id` / `milestone_id`, `criteria_condition: is`); `--status` and `--owner` are
applied client-side to the fetched page, as is `--limit`. The totals cover the matching rows of the
fetched page; when Zoho says more pages exist the output says so and hints `--page N+1`.

Open questions (no fixture or doc settles them): the `pattern` Zoho wants for two criteria at once (we
send "(1 and 2)"), and whether v3 task list items carry `end_date` (we show none when absent).
"""
from __future__ import annotations

import json

from ..errors import UsageError
from ..result import Hint, Result, Truncation
from . import _shape
from ._scope import resolve_ref, scope
from .base import Context

DEFAULT_LIMIT = 20
PER_PAGE = 100  # Zoho's own default and the largest page the rest of the CLI plans around


def int_param(ctx: Context, name: str, default: int) -> int:
    raw = ctx.params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise UsageError("usage.invalid_arguments", f"--{name} must be a whole number of at least 1, got {raw!r}",
                         hint=f"Pass a whole number, e.g. --{name} 5.")
    return value


def filter_param(criteria: list[tuple[str, str]]) -> str | None:
    if not criteria:
        return None
    body = [{"field_name": f, "criteria_condition": "is", "value": [v]} for f, v in criteria]
    pattern = "1" if len(body) == 1 else "(" + " and ".join(str(i + 1) for i in range(len(body))) + ")"
    return json.dumps({"criteria": body, "pattern": pattern})


def fetch_page(ctx: Context, portal: str, project: str, *, page: int = 1, criteria: list[tuple[str, str]] | None = None):
    """One page of project tasks through the cache; remembers each task's key -> id for `task <KEY>`."""
    params: dict[str, str] = {}  # Zoho's defaults (page 1, 100 per page) are not sent, so the plain call is one cache entry
    if page > 1:
        params["page"] = str(page)
    flt = filter_param(criteria or [])
    if flt:
        params["filter"] = flt
    return ctx.data.get("tasks", params=params or None, portal=portal, project=project)  # the cache learns the keys


def _matches_status(record: dict, wanted: str) -> bool:
    wanted = wanted.casefold().strip()
    if wanted in ("open", "closed"):
        return _shape.is_closed(record) == (wanted == "closed") or _shape.status_name(record).casefold() == wanted
    return _shape.status_name(record).casefold() == wanted


def tasks(ctx: Context) -> Result:
    limit = int_param(ctx, "limit", DEFAULT_LIMIT)
    page = int_param(ctx, "page", 1)
    portal, project = scope(ctx)

    criteria: list[tuple[str, str]] = []
    for flag, kind in (("tasklist", "tasklist"), ("milestone", "milestone")):
        if ctx.params.get(flag):
            criteria.append((f"{kind}_id", resolve_ref(ctx, kind, str(ctx.params[flag]), portal, project)))

    fetched = fetch_page(ctx, portal, project, page=page, criteria=criteria)
    matched = list(fetched)
    if ctx.params.get("status"):
        matched = [r for r in matched if _matches_status(r, str(ctx.params["status"]))]
    if ctx.params.get("owner"):
        wanted = str(ctx.params["owner"]).casefold().strip()
        matched = [r for r in matched if any(wanted in n.casefold() for n in _shape.owner_names(r))]

    shown = matched[:limit]
    counts: dict = {"matched": len(matched)}
    if len(shown) < len(matched):
        counts["shown"] = len(shown)
    counts.update(_shape.tally([_shape.status_name(r) for r in matched]))

    more = fetched.has_more is True or (fetched.has_more is None and len(fetched) >= PER_PAGE)
    own = {name: ctx.params[name] for name in ("tasklist", "milestone", "status", "owner") if ctx.params.get(name)}
    result = Result("task", [_shape.task_row(r) for r in shown], counts=counts)
    if len(shown) < len(matched):
        result.truncations.append(Truncation("tasks", len(matched) - len(shown), "rows", {**own, "limit": len(matched)}))
    if more:
        result.help.append(Hint("tasks", {**own, "page": page + 1}))
    if shown:
        result.help.append(Hint("task", {"ref": _shape.task_key(shown[0])}, positional=("ref",)))
    elif own:
        result.help.append(Hint("tasks"))
    return result
