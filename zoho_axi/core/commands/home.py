"""`home`: what `zoho-axi` prints with no arguments. Placeholder until T10 makes it live (default project,
my open counts); for now it reports the configured project and whether credentials are in place, offline."""
from __future__ import annotations

from ..result import Hint, Result
from ..status import auth_status
from .base import Context


def home(ctx: Context) -> Result:
    state = auth_status(ctx.settings, online=False)
    hints = [Hint("auth status")]
    if state["credentials"] != "complete":
        hints = [Hint("auth login-help"), *hints]
    payload = {
        "portal": ctx.settings.portal_id or "unset",
        "project": ctx.project or ctx.settings.project_id or "unset",
        "credentials": state["credentials"],
        "note": "placeholder: live project overview arrives with T10",
    }
    return Result("home", payload, help=hints)
