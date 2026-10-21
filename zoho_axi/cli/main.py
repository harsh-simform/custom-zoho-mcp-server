"""The CLI front-end: `main(argv, env, stdout, stderr) -> exit code`.

It holds no business logic: parse, build a Context, run the registered command, hand the Result to the
render layer, write the text. stdout carries the result only (errors included, as TOON); stderr carries
diagnostics (`--debug`). The function takes no stdin, so nothing in the CLI can read a terminal.
"""
from __future__ import annotations

import os
import sys
import traceback
from typing import Mapping, Sequence, TextIO

from ..core.commands.base import Context
from ..core.config import load_settings
from ..core.render import Rendered, render, render_error
from .help import command_help, top_help
from .parser import parse
from .registry import REGISTRY, Spec


def main(
    argv: Sequence[str],
    env: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
    registry: Sequence[Spec] = REGISTRY,
) -> int:
    fmt = "json" if "--json" in argv else "toon"  # so even a usage error honours --json
    ctx: Context | None = None
    debug = False
    try:
        inv = parse(argv, registry)
        debug = inv.debug
        if inv.help:
            stdout.write((command_help(inv.spec) if inv.spec else top_help(registry)) + "\n")
            return 0
        fmt = "json" if inv.json else "toon"
        ctx = Context(load_settings(env), inv.params or {}, project=inv.project, fresh=inv.fresh)
        assert inv.spec is not None  # only top-level help has no spec, and it returned above
        result = inv.spec.run(ctx)
        result.warnings = list(dict.fromkeys([*ctx.settings.warnings, *result.warnings]))
        rendered: Rendered = render(result, "cli", full=inv.full, fmt=fmt)
    except Exception as exc:  # the CLI never crashes with a traceback on stdout
        if debug:
            traceback.print_exc(file=stderr)
        rendered = render_error(exc, "cli", fmt)
    stdout.write(rendered.text + "\n")
    if debug:
        stderr.write(f"requests: {ctx.request_count() if ctx else 0}\n")
    return rendered.exit_code


def console_main() -> None:
    """The `zoho-axi` / `zoho` console-script entry point."""
    sys.exit(main(sys.argv[1:], os.environ, sys.stdout, sys.stderr))
