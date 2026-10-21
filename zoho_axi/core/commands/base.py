"""What every command receives. A command is a pure function `(Context) -> Result`; front-ends (the CLI
today, the MCP server in T13) build the Context and hand the Result to the render layer. Importing this
module has no side effects.

Data commands read Zoho through `ctx.data` (the response cache in front of the gateway), never through
`ctx.gateway` directly, so a warm call costs no request.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Mapping, TextIO

from ..config import Settings, resolve_project
from ..errors import UsageError
from ..result import Result

if TYPE_CHECKING:
    from ..attachments import ImageFile
    from ..cache import CachedGateway
    from ..gateway import ZohoGateway


@dataclass
class Context:
    settings: Settings
    params: Mapping[str, Any] = field(default_factory=dict)  # the command's own arguments and flags
    project: str | None = None  # --project / `project` arg; None means "the configured default"
    fresh: bool = False  # bypass cache reads (the response is still stored)
    full: bool = False  # --full: the renderer shows every field untruncated; commands lift their row caps
    _gateway: "ZohoGateway | None" = field(default=None, repr=False)
    env: Mapping[str, str] | None = field(default=None, repr=False)  # locates the cache file; None = os.environ
    today: date | None = None  # "today" for due-date logic; None = the current UTC date (tests pin it)
    _data: "CachedGateway | None" = field(default=None, repr=False)
    # Standard input, handed over by the front-end ONLY to a command that declares it (`Spec.stdin`); None for
    # every other command, so nothing else can read it. Use `read_stdin`, never the stream directly.
    stdin: TextIO | None = field(default=None, repr=False)
    # command name -> the flags it accepts (`{"task": {"--comments", ...}}`), so a command that suggests other
    # commands (`resolve`) never names a flag the command table does not know. None = not supplied.
    command_flags: Mapping[str, frozenset[str]] | None = field(default=None, repr=False)
    # MCP only: when a list, `images` downloads into memory (nothing is written to disk) and the delivered
    # `ImageFile`s are appended here for the front-end to return inline; None (the CLI) saves files and lists paths.
    image_sink: "list[ImageFile] | None" = field(default=None, repr=False)

    # Progress lines for the person at the terminal (the sign-in URL); the CLI wires stderr. None = say nothing.
    notify: Callable[[str], None] | None = field(default=None, repr=False)

    @property
    def gateway(self) -> "ZohoGateway":
        """One gateway per invocation, created on first use so commands that stay offline never build it."""
        if self._gateway is None:
            from ..gateway import ZohoGateway

            self._gateway = ZohoGateway(self.settings)
        return self._gateway

    @property
    def data(self) -> "CachedGateway":
        """The gateway behind the response cache; created on first use, like the gateway itself."""
        if self._data is None:
            from ..cache import CachedGateway, CacheStore, default_cache_path

            store = CacheStore(default_cache_path(os.environ if self.env is None else self.env))
            self._data = CachedGateway(self.gateway, store, self.settings, fresh=self.fresh)
        return self._data

    def request_count(self) -> int:
        """Zoho requests issued so far (0 if no command needed the gateway); shown by `--debug`."""
        return self._gateway.request_count if self._gateway is not None else 0

    def cache_warnings(self) -> list[str]:
        """Cache trouble that was absorbed (corrupt or locked database): surfaced as result warnings."""
        return list(self._data.warnings) if self._data is not None else []

    def close(self) -> None:
        if self._data is not None:
            self._data.close()

    def read_stdin(self, limit: int) -> str:
        """Up to `limit` characters of stdin. Never reads a terminal and never blocks on one: no stdin, a TTY,
        a closed stream or an empty pipe is a usage error (exit 2) and nothing is read."""
        stream, text = self.stdin, ""
        try:
            if stream is not None and not stream.closed and not stream.isatty():
                text = stream.read(limit)
        except (OSError, ValueError):
            pass
        if not text.strip():
            raise UsageError("usage.stdin_unavailable", "no text on stdin: it is a terminal, closed or empty.",
                             hint="Pipe the text in (`echo \"...\" | zoho-axi resolve -`) or pass it as an argument.")
        return text

    def current_date(self) -> date:
        return self.today or datetime.now(timezone.utc).date()

    def resolve_project(self) -> tuple[str, str]:
        return resolve_project(self.settings, self.project)


Command = Callable[[Context], Result]
