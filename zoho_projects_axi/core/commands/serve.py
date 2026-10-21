"""`zoho-projects-axi mcp`: the canonical MCP entry point (T15).

It is not a command in the usual sense: it produces no Result. The CLI front-end (cli/main.py) recognises a Spec
with `serve=True` and hands the process to the blocking stdio MCP server instead of running a command, so this
`run` is never called through the front-end. It exists so the Spec has a core command like every other, and
fails loudly should anything ever call it.
"""
from __future__ import annotations

from ..errors import UsageError
from ..result import Result
from .base import Context


def mcp(ctx: Context) -> Result:
    raise UsageError("usage.invalid_arguments", "`mcp` starts a stdio server and has no result to render.",
                     hint="Run it from an MCP client: `claude mcp add --scope user zoho -- zoho-projects-axi mcp`.")
