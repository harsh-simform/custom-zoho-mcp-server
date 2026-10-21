"""The CLI front-end: `main(argv, env, stdout, stderr) -> exit code`.

It holds no business logic: parse, build a Context, run the registered command, hand the Result to the
render layer, write the text. stdout carries the result only (errors included, as TOON); stderr carries
diagnostics (`--debug`). Stdin is an optional parameter handed ONLY to a command whose Spec says `stdin=True`
(`resolve -`); every other command gets None, and a terminal is never read (see `Context.read_stdin`).

`registry`, `gateway`, `today` and `serve` are injection points for tests: a fake command table, a gateway over
a replay transport, a pinned date, and a stand-in for the blocking MCP server that `zoho-axi mcp` hands over to.
Production passes none of them.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
import warnings
from datetime import date
from typing import TYPE_CHECKING, Callable, Mapping, Sequence, TextIO

from ..core.commands.base import Context
from ..core.config import load_settings
from ..core.render import Rendered, render, render_error
from ..core.runner import execute, make_context
from .help import command_help, top_help
from .parser import parse
from .registry import REGISTRY, Spec

if TYPE_CHECKING:
    from ..core.gateway import ZohoGateway

HOOK_DEADLINE_SECONDS = 5.0  # a SessionStart hook must not hold a session up; past this it gives up silently
_HELP = ("--help", "-h")


def _run_hook(spec: Spec, env: Mapping[str, str], stdout: TextIO, gateway: "ZohoGateway | None", today: date | None) -> int:
    """Run a hook entrypoint (`Spec.hook`): its text goes to stdout, and in every other case (nothing to say,
    any exception, a hung network call) nothing does. stderr is never written, logging is muted for the run,
    and the exit code is always 0: Claude Code shows a hook that fails or prints to stderr as an error."""
    box: list[str] = []

    def work() -> None:
        ctx: Context | None = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ctx = Context(load_settings(env), {}, _gateway=gateway, env=env, today=today)
                box.append(render(spec.run(ctx), "cli").text)
        except Exception:
            pass
        finally:
            try:
                if ctx:
                    ctx.close()
            except Exception:
                pass

    logging.disable(logging.CRITICAL)
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(HOOK_DEADLINE_SECONDS)
    finished = not worker.is_alive()
    if finished:
        logging.disable(logging.NOTSET)  # a hung worker keeps logging muted: the process is about to exit
    if finished and box and box[0]:
        try:
            stdout.write(box[0] + "\n")
        except Exception:
            pass
    return 0


def _serve_mcp() -> None:
    """Hand the process to the stdio MCP server. Imported here, not at module level: the MCP SDK is slow to
    import and only this one command needs it. The server reads its settings from the real environment."""
    from ..mcp_server.server import main as serve_main

    serve_main()


def _run_server(serve: Callable[[], None], stderr: TextIO) -> int:
    """Run the blocking server. stdout belongs to the MCP wire, so nothing is written to it here: a crash is
    one line on stderr and exit 1, Ctrl-C (or the client closing the pipe by interrupt) is a clean stop."""
    try:
        serve()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        stderr.write(f"zoho-axi mcp: server stopped: {exc}\n")
        return 1
    return 0


def main(
    argv: Sequence[str],
    env: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
    registry: Sequence[Spec] = REGISTRY,
    gateway: "ZohoGateway | None" = None,
    today: date | None = None,
    stdin: TextIO | None = None,
    serve: Callable[[], None] | None = None,
) -> int:
    fmt = "json" if "--json" in argv else "toon"  # so even a usage error honours --json
    ctx: Context | None = None
    debug = False
    hook = next((s for s in registry if s.hook and argv and argv[0] == s.name), None)
    if hook and not any(t in _HELP for t in argv):
        return _run_hook(hook, env, stdout, gateway, today)  # before parsing: a hook never fails on its arguments
    try:
        inv = parse(argv, registry)
        debug = inv.debug
        if inv.help:
            stdout.write((command_help(inv.spec) if inv.spec else top_help(registry)) + "\n")
            return 0
        if inv.spec is not None and inv.spec.hook:  # reached with a global flag in front (`--json session-start`)
            return _run_hook(inv.spec, env, stdout, gateway, today)
        if inv.spec is not None and inv.spec.serve:  # `zoho-axi mcp`: no Result, no stdout; the server owns both
            return _run_server(serve or _serve_mcp, stderr)
        fmt = "json" if inv.json else "toon"
        assert inv.spec is not None  # only top-level help has no spec, and it returned above
        ctx = make_context(inv.spec, registry, load_settings(env), inv.params or {}, project=inv.project,
                           fresh=inv.fresh, full=inv.full, gateway=gateway, env=env, today=today, stdin=stdin)
        rendered: Rendered = execute(inv.spec, ctx, "cli", fmt)
    except Exception as exc:  # the CLI never crashes with a traceback on stdout
        if debug:
            traceback.print_exc(file=stderr)
        rendered = render_error(exc, "cli", fmt)
    stdout.write(rendered.text + "\n")
    if debug:
        stderr.write(f"requests: {ctx.request_count() if ctx else 0}\n")
    if ctx:
        ctx.close()
    return rendered.exit_code


def console_main() -> None:
    """The `zoho-axi` / `zoho` console-script entry point."""
    sys.exit(main(sys.argv[1:], os.environ, sys.stdout, sys.stderr, stdin=sys.stdin))
