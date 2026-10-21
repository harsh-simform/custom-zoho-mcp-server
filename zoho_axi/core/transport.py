"""The transport seam: one `send` call that moves a single HTTP request over the wire and reports
`(status, body)`. Adapters: `http_transport.RequestsTransport` (real) and the fixture replay in
`tests/support/replay.py`. Nothing in this module imports an HTTP library.

Everything above this seam (guard, limiter, retry, fallback, normalization) lives in the gateway,
so every adapter gets identical behavior."""
from __future__ import annotations

from typing import Mapping, NamedTuple, Protocol

from .errors import ReadOnlyError


class TransportResponse(NamedTuple):
    status: int
    body: bytes
    content_type: str = ""


class TransportError(OSError):
    """The request never produced an HTTP response (DNS, connect, TLS, timeout). Never carries the
    URL, headers or underlying message: those can hold credentials."""


class PayloadTooLarge(Exception):
    """The response body exceeded `max_bytes` while it was being read."""

    def __init__(self, max_bytes: int):
        super().__init__(f"response exceeds {max_bytes} bytes")
        self.max_bytes = max_bytes


class Transport(Protocol):
    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None,
        timeout: float,
        *,
        data: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> TransportResponse:
        """`data` (form body) exists only for the OAuth token refresh; `max_bytes` makes the adapter
        stop reading and raise `PayloadTooLarge` rather than buffer an oversized body."""
        ...


class ReadOnlyTransport:
    """Wraps any transport so that only GET reaches it. The one exception is a POST to a URL in
    `allow_post_urls` (the OAuth token endpoint). The refusal happens before the inner transport is
    touched, so it is structural, not a convention."""

    def __init__(self, inner: Transport, allow_post_urls: tuple[str, ...] = ()):
        self._inner = inner
        self._allow_post_urls = allow_post_urls

    def check(self, method: str, url: str) -> None:
        verb = str(method).upper()
        if verb != "GET" and not (verb == "POST" and url in self._allow_post_urls):
            raise ReadOnlyError(
                "guard.read_only",
                f"Refusing {verb} request: zoho-axi is read-only.",
                hint="Only GET requests (and the OAuth token refresh) are ever sent to Zoho.",
            )

    def send(self, method, url, headers, params, timeout, *, data=None, max_bytes=None) -> TransportResponse:
        self.check(method, url)
        return self._inner.send(str(method).upper(), url, headers, params, timeout, data=data, max_bytes=max_bytes)
