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
from typing import TYPE_CHECKING, Any, Callable, Mapping

from ..config import Settings, resolve_project
from ..result import Result

if TYPE_CHECKING:
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

    def current_date(self) -> date:
        return self.today or datetime.now(timezone.utc).date()

    def resolve_project(self) -> tuple[str, str]:
        return resolve_project(self.settings, self.project)


Command = Callable[[Context], Result]
