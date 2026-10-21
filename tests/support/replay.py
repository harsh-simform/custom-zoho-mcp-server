"""Fixture-replay transport: the second adapter behind the gateway's transport seam.

Register the responses a scenario expects with `add(...)`; every request the code under test makes
is matched against them (method, url, params) and recorded in `calls`. A request nobody registered
raises `UnrecordedRequest`, so a test can never silently hit "the network". T04 builds the
recorder/sanitizer that produces entries for `from_entries`.
"""
from __future__ import annotations

import json as jsonlib
from typing import Any, Mapping, NamedTuple

from zoho_projects_axi.core.transport import PayloadTooLarge, TransportResponse


class UnrecordedRequest(Exception):
    pass


class Call(NamedTuple):
    method: str
    url: str
    headers: dict
    params: dict | None
    data: dict | None
    timeout: float
    max_bytes: int | None


class _Entry:
    def __init__(self, method, url, params, response, error, repeat):
        self.method, self.url, self.params = method, url, params
        self.response, self.error, self.repeat = response, error, repeat
        self.used = False


def _norm(params: Mapping | None) -> dict | None:
    return dict(params) if params else None


class ReplayTransport:
    def __init__(self) -> None:
        self._entries: list[_Entry] = []
        self.calls: list[Call] = []

    def add(
        self,
        method: str,
        url: str,
        *,
        params: Mapping | None = None,
        status: int = 200,
        json: Any = None,
        body: bytes | None = None,
        content_type: str | None = None,
        error: BaseException | None = None,
        repeat: bool = False,
    ) -> "ReplayTransport":
        """Entries for the same request are consumed in the order added, so a scenario can say
        "first 429, then 200". `repeat=True` keeps the entry available forever (for bulk calls)."""
        if json is not None:
            body = jsonlib.dumps(json).encode()
            content_type = content_type or "application/json"
        response = TransportResponse(status, body or b"", content_type or "")
        self._entries.append(_Entry(method.upper(), url, _norm(params), response, error, repeat))
        return self

    def get(self, url: str, **kwargs) -> "ReplayTransport":
        return self.add("GET", url, **kwargs)

    @classmethod
    def from_entries(cls, entries: list[Mapping]) -> "ReplayTransport":
        """Entries are `{method, url, params?, status?, json?|body?, content_type?}` dicts."""
        replay = cls()
        for entry in entries:
            replay.add(entry.get("method", "GET"), entry["url"], **{
                k: v for k, v in entry.items() if k in ("params", "status", "json", "body", "content_type")
            })
        return replay

    @property
    def unused(self) -> list[tuple[str, str]]:
        return [(e.method, e.url) for e in self._entries if not e.used and not e.repeat]

    def send(self, method, url, headers, params, timeout, *, data=None, max_bytes=None) -> TransportResponse:
        self.calls.append(Call(method.upper(), url, dict(headers), _norm(params), _norm(data), timeout, max_bytes))
        wanted = (method.upper(), url, _norm(params))
        for entry in self._entries:
            if (entry.method, entry.url, entry.params) == wanted and (entry.repeat or not entry.used):
                entry.used = True
                if entry.error is not None:
                    raise entry.error
                if max_bytes is not None and len(entry.response.body) > max_bytes:
                    raise PayloadTooLarge(max_bytes)
                return entry.response
        raise UnrecordedRequest(f"no recorded response for {wanted[0]} {url} params={wanted[2]}")
