"""What every command receives. A command is a pure function `(Context) -> Result`; front-ends (the CLI
today, the MCP server in T13) build the Context and hand the Result to the render layer. Importing this
module has no side effects."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Mapping

from ..config import Settings, resolve_project
from ..result import Result

if TYPE_CHECKING:
    from ..gateway import ZohoGateway


@dataclass
class Context:
    settings: Settings
    params: Mapping[str, Any] = field(default_factory=dict)  # the command's own arguments and flags
    project: str | None = None  # --project / `project` arg; None means "the configured default"
    fresh: bool = False  # bypass the cache (the cache itself arrives in T07)
    _gateway: "ZohoGateway | None" = field(default=None, repr=False)

    @property
    def gateway(self) -> "ZohoGateway":
        """One gateway per invocation, created on first use so commands that stay offline never build it."""
        if self._gateway is None:
            from ..gateway import ZohoGateway

            self._gateway = ZohoGateway(self.settings)
        return self._gateway

    def request_count(self) -> int:
        """Zoho requests issued so far (0 if no command needed the gateway); shown by `--debug`."""
        return self._gateway.request_count if self._gateway is not None else 0

    def resolve_project(self) -> tuple[str, str]:
        return resolve_project(self.settings, self.project)


Command = Callable[[Context], Result]
