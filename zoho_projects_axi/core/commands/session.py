"""`zoho-projects-axi session-start`: the Claude Code SessionStart hook entrypoint (T14, AXI principle 7).

Claude Code adds the stdout of an exit-0 SessionStart command to the session's context, so this prints
a few lines (budget: 150 tokens) about the default project: its name and status, a short tally of open
tasks, and the three command forms worth knowing. It reads the response cache first. Entries count as
fresh here for `HOOK_MAX_AGE_SECONDS` (much longer than the task TTL `home` uses: a snapshot is the point),
and only an older or missing entry costs a request, and only when credentials exist. It reuses `home`'s
own requests, so the hook and `zoho-projects-axi` share cache entries.

Whenever there is nothing good to say it raises `Silent`; the CLI front-end (cli/main.py) turns that, and
every other exception, into empty output and exit 0. A hook must never break a session.
"""
from __future__ import annotations

import dataclasses

from ..result import Hint, Result
from ..status import auth_status
from . import home
from .base import Context

HOOK_MAX_AGE_SECONDS = 4 * 3600
MAX_COUNTS = 6  # keeps the tally short even for a project with many custom statuses


class Silent(Exception):
    """Nothing to report (unconfigured, nothing cached, partial data); not an error."""


def session_start(ctx: Context) -> Result:
    settings = ctx.settings
    if auth_status(settings, online=False)["credentials"] != "complete":
        raise Silent("credentials missing")
    if not (settings.portal_id and settings.project_id):
        raise Silent("no default project")
    # Serve the cache for the hook's own max age (never shorter than the user's configured TTLs).
    ctx.settings = dataclasses.replace(
        settings,
        ttl_long_seconds=max(settings.ttl_long_seconds, HOOK_MAX_AGE_SECONDS),
        ttl_task_seconds=max(settings.ttl_task_seconds, HOOK_MAX_AGE_SECONDS),
        ttl_comment_seconds=max(settings.ttl_comment_seconds, HOOK_MAX_AGE_SECONDS),
    )
    live = home.home(ctx)
    payload = live.payload
    if not isinstance(payload, dict) or not payload.get("name") or not live.counts:
        raise Silent("incomplete data")  # offline in disguise: the task page could not be read

    due = payload.get("due") or []
    snapshot = {
        "project": payload["project"],
        "name": payload["name"],
        "status": payload.get("status", ""),
        "note": "cached snapshot; run `zoho-projects-axi` for live data",
    }
    hints = [
        Hint("resolve", {"text": "<text>"}, positional=("text",)),
        Hint("task", {"ref": due[0]["key"] if due else "<KEY>"}, positional=("ref",)),
        Hint("tasks", {"status": "open"}),
    ]
    return Result("session", snapshot, counts=dict(list(live.counts.items())[:MAX_COUNTS]), help=hints)
