"""The MCP front-end: six tools, a thin shim over the same command runner the CLI uses.

Each tool is one registry `Spec` (`project`, `tasks`, `task`, `bug`, `people`, `resolve`): its description is the
spec's summary and its arguments are the spec's positional args and flags plus the global flags `project`,
`fresh`, `full` and `format` (toon | json, the CLI's `--json`). They are derived, so they cannot drift from the
CLI. A call builds a Context, runs `core.runner.execute` in "mcp" mode (the only difference from the CLI is that
next-step hints read as tool calls) and returns the same text. `images=true` returns the images inline as `Image`
content after the text (default false; nothing is written to disk). A failure is a tool error (`is_error`) whose
text is the same structured `error / message / hint` block the CLI prints.

Stdout is the MCP wire: logging goes to stderr (and the optional log file), with credentials redacted. Settings are
loaded once, in `main()`, never at import. The gateway (and so the OAuth token and the rate limiter) is shared by
all calls; calls are serialized because the command layer is synchronous and the cache is per call.
"""
from __future__ import annotations

import inspect
import logging
import os
import sys
import threading
from datetime import date
from typing import Any, Literal, Mapping, Sequence

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.tools.base import Tool
from mcp_types import CallToolResult, TextContent

from ..cli.registry import GLOBAL_FLAGS, REGISTRY, Spec
from ..core.config import Settings, load_settings
from ..core.errors import ZohoAxiError
from ..core.gateway import ZohoGateway
from ..core.render import render_error
from ..core.runner import execute, make_context

SERVER_NAME = "custom-zoho-project-mcp-server"
TOOLS = ("project", "tasks", "task", "bug", "people", "resolve")  # `project list|use` and the rest stay CLI-only
_GLOBAL_TOOL_FLAGS = {"--project", "--fresh", "--full"}  # --json becomes `format`; --debug is CLI-only
_INTEGER_METAVAR = "N"  # a value flag whose metavar is N takes a whole number (--limit, --page)
_IMAGES_NOTE = " images=true returns the images inline."

logger = logging.getLogger(__name__)
tool_logger = logging.getLogger("zoho_axi.mcp.tools")


# --- logging: stderr only, secrets redacted --------------------------------------------------------------------

class RedactSecrets(logging.Filter):
    """Replaces every configured credential value in a log record with `***`. Nothing logs them on purpose;
    this is the belt to that pair of braces."""

    def __init__(self, secrets: Sequence[str | None]):
        super().__init__()
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secrets:
            message = record.getMessage()
            scrubbed = message
            for secret in self._secrets:
                scrubbed = scrubbed.replace(secret, "***")
            if scrubbed != message:
                record.msg, record.args = scrubbed, None
        return True


def setup_logging(settings: Settings | None) -> None:
    """Root logging to stderr (and `log_file` when set). Called once from `main()`, never at import."""
    handlers: list[logging.Handler] = [logging.StreamHandler(stream=sys.stderr)]
    if settings and settings.log_file:
        handlers.append(logging.FileHandler(settings.log_file))
    formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S%z")
    secrets = [settings.client_id, settings.client_secret, settings.refresh_token] if settings else []
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(RedactSecrets(secrets))
    root = logging.getLogger()
    root.handlers = handlers
    level = (settings.log_level if settings else "INFO").upper()
    root.setLevel(level if isinstance(logging.getLevelName(level), int) else "INFO")
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# --- what the tools accept, derived from the registry ----------------------------------------------------------

def _parameters(spec: Spec) -> list[inspect.Parameter]:
    kw = inspect.Parameter.KEYWORD_ONLY
    params = [inspect.Parameter(a.name, kw, annotation=str, **({} if a.required else {"default": ""}))
              for a in spec.args]
    for flag in (*spec.flags, *(f for f in GLOBAL_FLAGS if f.name in _GLOBAL_TOOL_FLAGS)):
        if not flag.metavar:
            params.append(inspect.Parameter(flag.dest, kw, annotation=bool, default=False))
        elif flag.metavar == _INTEGER_METAVAR:
            params.append(inspect.Parameter(flag.dest, kw, annotation="int | None", default=None))
        else:
            params.append(inspect.Parameter(flag.dest, kw, annotation="str | None", default=None))
    params.append(inspect.Parameter("format", kw, annotation=Literal["toon", "json"], default="toon"))
    return params


def _description(spec: Spec) -> str:
    text = spec.summary[0].upper() + spec.summary[1:] + "."
    return text + (_IMAGES_NOTE if any(f.name == "--images" for f in spec.flags) else "")


def _slim(schema: dict[str, Any]) -> dict[str, Any]:
    """The same input schema without what only costs tokens: titles, `default: false/null` and the null branch
    of an optional (the server still accepts null). Every tool schema is paid for in every session."""
    slim: dict[str, Any] = {"type": "object", "properties": {}}
    for name, prop in schema.get("properties", {}).items():
        prop = {k: v for k, v in prop.items() if k != "title"}
        if prop.get("default") in (False, None):
            prop.pop("default", None)
        if "anyOf" in prop:
            branches = [b for b in prop["anyOf"] if b.get("type") != "null"]
            if len(branches) == 1:
                prop = {**{k: v for k, v in prop.items() if k != "anyOf"}, **branches[0]}
        slim["properties"][name] = prop
    if schema.get("required"):
        slim["required"] = schema["required"]
    return slim


# --- running a call --------------------------------------------------------------------------------------------

class _Runtime:
    """What every tool call shares: settings (or why they could not load), one lazily built gateway, a lock."""

    def __init__(self, settings: Settings | None, startup_error: ZohoAxiError | None, gateway: ZohoGateway | None,
                 env: Mapping[str, str] | None, today: date | None, registry: Sequence[Spec]):
        self.settings, self.startup_error = settings, startup_error
        self.gateway, self.env, self.today, self.registry = gateway, env, today, registry
        self._lock = threading.Lock()

    def call(self, spec: Spec, args: dict[str, Any]) -> Any:
        fmt = args.pop("format", "toon")
        safe = {k: v for k, v in args.items() if v not in (None, False, "")}
        tool_logger.info("-> %s(%s)", spec.name, safe)
        with self._lock:
            ctx = None
            try:
                if self.settings is None:
                    raise self.startup_error or RuntimeError("settings were not loaded")
                if self.gateway is None:
                    self.gateway = ZohoGateway(self.settings)
                sink: list | None = [] if args.get("images") else None
                own = {a.name for a in spec.args} | {f.dest for f in spec.flags}
                ctx = make_context(
                    spec, self.registry, self.settings, {k: v for k, v in args.items() if k in own},
                    project=args.get("project") or None, fresh=bool(args.get("fresh")), full=bool(args.get("full")),
                    gateway=self.gateway, env=self.env, today=self.today, image_sink=sink)
                rendered = execute(spec, ctx, "mcp", fmt)
            except Exception as exc:
                if isinstance(exc, ZohoAxiError):
                    tool_logger.info("x  %s failed: %s", spec.name, exc.code)
                else:
                    tool_logger.exception("x  %s raised", spec.name)
                return CallToolResult(content=[TextContent(type="text", text=render_error(exc, "mcp", fmt).text)],
                                      is_error=True)
            finally:
                if ctx is not None:
                    ctx.close()
        tool_logger.info("<- %s ok", spec.name)
        if not sink:
            return rendered.text
        return [rendered.text, *(Image(data=i.data, format=i.content_type.split("/", 1)[-1]) for i in sink if i.data)]


def _tool(spec: Spec, runtime: _Runtime) -> Tool:
    def run(**kwargs: Any) -> Any:
        return runtime.call(spec, kwargs)

    run.__name__ = spec.name
    run.__signature__ = inspect.Signature(_parameters(spec))  # type: ignore[attr-defined]
    tool = Tool.from_function(run, name=spec.name, description=_description(spec), structured_output=False)
    tool.parameters = _slim(tool.parameters)
    return tool


def build_server(
    settings: Settings | None,
    *,
    gateway: ZohoGateway | None = None,
    env: Mapping[str, str] | None = None,
    today: date | None = None,
    registry: Sequence[Spec] = REGISTRY,
    startup_error: ZohoAxiError | None = None,
) -> MCPServer:
    """The server for `settings`. `gateway`, `env` (where the cache lives), `today` and `registry` are injection
    points for tests. With `settings=None` every call answers `startup_error` (a config file that would not load)."""
    runtime = _Runtime(settings, startup_error, gateway, env, today, registry)
    specs = {s.name: s for s in registry}
    return MCPServer(SERVER_NAME, tools=[_tool(specs[name], runtime) for name in TOOLS])


def main() -> None:
    """Console entry (`zoho-projects-mcp`, `python -m zoho_mcp`): credentials come from the config file or
    environment; nothing has to be configured first."""
    settings, error = None, None
    try:
        settings = load_settings(os.environ)
    except ZohoAxiError as exc:  # keep the server up so the client sees the reason instead of a dead process
        error = exc
    setup_logging(settings)
    if error is not None:
        logger.error("settings did not load: %s", error.message)
    logger.info("zoho-projects MCP server starting")
    try:
        build_server(settings, startup_error=error).run()
    finally:
        logger.info("zoho-projects MCP server stopped")


if __name__ == "__main__":
    main()
