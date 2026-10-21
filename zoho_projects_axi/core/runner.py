"""The one place a command is run and rendered, shared by the CLI and the MCP front-end.

`execute(spec, ctx, mode, fmt)` runs the command named by `spec` against a ready `Context`, folds the settings
and cache warnings into the result and renders it. It raises on failure (a `ZohoAxiError` or anything else);
each front-end decides how an error is shown (the CLI prints it and exits non-zero, MCP returns a tool error).
`make_context` builds the Context both front-ends need from their already-parsed inputs.
Importing this module has no side effects.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Mapping, Sequence, TextIO

from .commands.base import Context
from .config import Settings
from .render import Rendered, render

if TYPE_CHECKING:
    from ..cli.registry import Spec
    from .gateway import ZohoGateway


def make_context(
    spec: "Spec",
    registry: Sequence["Spec"],
    settings: Settings,
    params: Mapping[str, Any],
    *,
    project: str | None = None,
    fresh: bool = False,
    full: bool = False,
    gateway: "ZohoGateway | None" = None,
    env: Mapping[str, str] | None = None,
    today: date | None = None,
    stdin: TextIO | None = None,
    image_sink: list | None = None,
) -> Context:
    return Context(settings, dict(params), project=project, fresh=fresh, full=full, _gateway=gateway, env=env,
                   today=today, stdin=stdin if spec.stdin else None,
                   image_sink=image_sink, command_flags={s.name: frozenset(f.name for f in s.flags) for s in registry})


def execute(spec: "Spec", ctx: Context, mode: str = "cli", fmt: str = "toon") -> Rendered:
    result = spec.run(ctx)
    result.warnings = list(dict.fromkeys([*ctx.settings.warnings, *result.warnings, *ctx.cache_warnings()]))
    return render(result, mode, full=ctx.full, fmt=fmt)
