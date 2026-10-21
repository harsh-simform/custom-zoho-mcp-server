"""`auth status` and `auth login-help`: T01's functions, shaped into Results."""
from __future__ import annotations

import html

from ..auth import ZohoAuth
from ..result import Hint, Result
from ..status import auth_status, login_help
from .base import Context


def status(ctx: Context) -> Result:
    online = not ctx.params.get("offline")
    auth = ZohoAuth(ctx.settings, post=ctx.gateway.token_post) if online else None
    data = auth_status(ctx.settings, auth=auth, online=online)
    data.pop("warnings", None)  # settings warnings are added to every Result by the front-end
    hints = []
    if data["credentials"] != "complete":
        hints.append(Hint("auth login-help"))
    return Result("auth", data, help=hints)


def login_steps(ctx: Context) -> Result:
    # The renderer strips HTML-looking text (Zoho's comment bodies); escape so `<module>` / `<KEY>` placeholders survive.
    rows = [{"text": html.escape(line, quote=False)} for line in login_help(ctx.settings).splitlines()]
    return Result("step", rows, help=[Hint("auth status")])
