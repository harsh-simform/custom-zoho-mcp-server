"""`auth status`, `auth login` and `auth login-help`: T01's functions, shaped into Results."""
from __future__ import annotations

import html
import webbrowser

from .. import oauth_login
from ..auth import ZohoAuth
from ..config_edit import set_refresh_token
from ..errors import UsageError
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


def login(ctx: Context) -> Result:
    raw = ctx.params.get("port")
    try:
        port = oauth_login.DEFAULT_PORT if raw is None else int(raw)
    except ValueError:
        port = 0
    if not 1024 <= port <= 65535:
        raise UsageError("usage.invalid_arguments", f"--port must be a whole number from 1024 to 65535, got {raw!r}",
                         hint="Pass an unused port, e.g. --port 8765, and register http://localhost:PORT/callback.")

    notify = ctx.notify or (lambda _line: None)
    refresh_token = oauth_login.authorize(
        ctx.settings, ctx.gateway.token_post, port=port, announce=notify,
        open_browser=None if ctx.params.get("no_browser") else webbrowser.open,
    )
    set_refresh_token(ctx.settings.config_path, refresh_token)

    warnings = []
    if ctx.settings.sources.get("refresh_token") in ("flag", "env"):
        warnings.append("refresh_token is also set in the environment, which overrides the config file; "
                        "unset ZOHO_AXI_REFRESH_TOKEN to use the new one")
    data = {"config_file": str(ctx.settings.config_path), "refresh_token": "saved", "scopes": "read-only"}
    return Result("auth", data, warnings=warnings, help=[Hint("auth status"), Hint("project list")])


def login_steps(ctx: Context) -> Result:
    # The renderer strips HTML-looking text (Zoho's comment bodies); escape so `<module>` / `<KEY>` placeholders survive.
    rows = [{"text": html.escape(line, quote=False)} for line in login_help(ctx.settings).splitlines()]
    return Result("step", rows, help=[Hint("auth status")])
