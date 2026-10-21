"""The Zoho gateway: callers name a resource and its ids and get normalized entities back.

It owns everything between a command and the wire: the resource table, the v3 -> legacy fallback,
error classification, normalization. The wire itself is the transport seam (`transport.py`), so the
same code runs against real HTTP or recorded fixtures. Logging goes to stderr only and never
includes tokens or credentials.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlparse

from .auth import ZohoAuth
from .config import Settings
from .errors import AuthError, NotFoundError, RateLimitedError, UpstreamError
from .limiter import DEFAULT_LIMIT, DEFAULT_MAX_WAIT_SECONDS, DEFAULT_WINDOW_SECONDS, RateLimiter
from .resources import RESOURCES, Resource, normalize
from .transport import PayloadTooLarge, ReadOnlyTransport, Transport, TransportResponse

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 3  # per request, when Zoho answers 429


def _api_message(body: bytes) -> str:
    """A short, safe excerpt of Zoho's own error text, if it sent one."""
    try:
        data = json.loads(body)
    except ValueError:
        return ""
    if isinstance(data, dict):
        err = data.get("error")
        for candidate in (err.get("message") if isinstance(err, dict) else err, data.get("message")):
            if isinstance(candidate, str) and candidate:
                return f": {candidate[:200]}"
    return ""


class ZohoGateway:
    def __init__(
        self,
        settings: Settings,
        transport: Transport | None = None,
        *,
        token_provider: Callable[[], str] | None = None,
        token_invalidator: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rate_limit: int = DEFAULT_LIMIT,
        rate_window: float = DEFAULT_WINDOW_SECONDS,
        max_wait: float = DEFAULT_MAX_WAIT_SECONDS,
    ):
        self._sleep = sleep
        self._limiter = RateLimiter(rate_limit, rate_window, max_wait, clock=clock, sleep=sleep)
        self._requests = 0
        self._settings = settings
        self._transport = transport
        self._token_provider = token_provider
        self._token_invalidator = token_invalidator  # how to drop a token Zoho rejected; None: cannot retry a 401
        self._auth: ZohoAuth | None = None

    # --- plumbing ---

    @property
    def request_count(self) -> int:
        """Requests actually sent to the transport so far: retries, legacy fallbacks and the token
        refresh all count. Used by `--debug` and the "<= N calls" acceptance tests."""
        return self._requests

    def _wire(self) -> ReadOnlyTransport:
        if not isinstance(self._transport, ReadOnlyTransport):
            if self._transport is None:
                from .http_transport import RequestsTransport

                self._transport = RequestsTransport()
            self._transport = ReadOnlyTransport(
                self._transport, allow_post_urls=(f"{self._settings.accounts_base}/oauth/v2/token",)
            )
        return self._transport  # type: ignore[return-value]

    def _dispatch(self, method, url, headers, params, timeout, *, data=None, max_bytes=None) -> TransportResponse:
        """The one path to the wire: guard (inside the wrapped transport), limiter, 429 retry."""
        wire = self._wire()
        wire.check(method, url)  # refuse before touching the limiter, the counter or the network
        for attempt in range(MAX_ATTEMPTS):
            self._limiter.acquire()
            self._requests += 1
            response = wire.send(method, url, headers, params, timeout, data=data, max_bytes=max_bytes)
            if response.status != 429:
                return response
            if attempt < MAX_ATTEMPTS - 1:
                delay = 2**attempt
                logger.warning("429 from Zoho, backing off %ss", delay)
                self._sleep(delay)
        raise RateLimitedError(
            "rate_limited.zoho",
            f"Zoho kept answering 429 after {MAX_ATTEMPTS} attempts.",
            hint="Wait a few minutes before retrying; Zoho locks the API for 30 minutes if its limit is breached.",
        )

    def _token(self) -> str:
        if self._token_provider is None:
            self._auth = self._auth or ZohoAuth(self._settings, post=self.token_post)
            self._token_provider = self._auth.get_access_token
            self._token_invalidator = self._auth.invalidate
        return self._token_provider()

    def _fetch(self, url: str, params: Mapping[str, str] | None, max_bytes: int | None = None) -> TransportResponse:
        headers = {"Authorization": f"Zoho-oauthtoken {self._token()}"}
        logger.debug("GET %s", url)
        return self._dispatch("GET", url, headers, params, REQUEST_TIMEOUT_SECONDS, max_bytes=max_bytes)

    # --- S1: resource -> normalized entity ---

    def get(self, resource: str, /, *, params: Mapping[str, str] | None = None, **ids: Any):
        spec = RESOURCES.get(resource)
        if spec is None:
            raise ValueError(f"unknown resource {resource!r}; known: {', '.join(sorted(RESOURCES))}")
        needed = spec.id_names
        if set(ids) != needed:
            raise ValueError(
                f"resource {resource!r} needs ids {sorted(needed)}, got {sorted(ids)}"
            )
        quoted = {k: quote(str(v), safe="") for k, v in ids.items()}
        label = f"{resource} " + " ".join(f"{k}={v}" for k, v in ids.items())

        response = self._get_with_fallback(spec, quoted, params, label)
        return normalize(spec, self._decode(response, label))

    def _get_with_fallback(self, spec: Resource, ids: Mapping[str, str], params, label: str) -> TransportResponse:
        v3_url = f"{self._settings.api_v3_base}{spec.v3.format(**ids)}" if spec.v3 else None
        legacy_url = f"{self._settings.restapi_base}{spec.legacy.format(**ids)}" if spec.legacy else None
        first = v3_url or legacy_url
        assert first is not None
        response = self._request(first, params, label)
        if response.status == 404 and v3_url and legacy_url:
            logger.warning("v3 path returned 404, falling back to the legacy path for %s", label)
            response = self._request(legacy_url, params, label)
        return self._check(response, label)

    def _request(self, url: str, params, label: str, max_bytes: int | None = None) -> TransportResponse:
        try:
            response = self._fetch(url, params, max_bytes)
            if response.status == 401 and self._token_invalidator is not None:
                # The cached token is dead (revoked, or expired early): drop it and retry this request ONCE
                # with a fresh one. A long-lived `zoho-axi mcp` would otherwise fail every call until expiry.
                logger.warning("401 from Zoho, refreshing the access token and retrying once")
                self._token_invalidator()
                response = self._fetch(url, params, max_bytes)
            return response
        except PayloadTooLarge:
            raise self._too_large(max_bytes) from None
        except OSError as exc:
            raise UpstreamError(
                "upstream.unreachable",
                f"Could not reach Zoho: {type(exc).__name__}",
                hint="Check your network connection and the `dc` (data center) setting, then retry.",
            ) from None

    def _check(self, response: TransportResponse, label: str) -> TransportResponse:
        status = response.status
        if 200 <= status < 300:
            return response
        if status == 404:
            raise NotFoundError("not_found", f"{label}: not found", hint="Check the id and the project, then retry.")
        if status == 401:
            raise AuthError(
                "auth.unauthorized", "Zoho rejected the access token (HTTP 401).",
                hint="Run `zoho-axi auth status`; the refresh token or client settings may be wrong.",
            )
        if status == 403:
            raise AuthError(
                "auth.forbidden", f"{label}: forbidden (HTTP 403)",
                hint="The OAuth client lacks a required ZohoProjects.*.READ scope, or you lack access.",
            )
        raise UpstreamError(
            f"upstream.http_{status}", f"{label}: Zoho returned HTTP {status}{_api_message(response.body)}",
            hint="Retry in a moment; if it persists, check the id and the Zoho status page.",
        )

    @staticmethod
    def _decode(response: TransportResponse, label: str) -> Any:
        if not response.body.strip():
            return None
        try:
            return json.loads(response.body)
        except ValueError:
            raise UpstreamError(
                "upstream.bad_response", f"{label}: Zoho returned a non-JSON body (HTTP {response.status})",
                hint="Retry in a moment.",
            ) from None

    # --- S3: file download ---

    @staticmethod
    def _too_large(max_bytes: int | None) -> UpstreamError:
        return UpstreamError(
            "download.too_large", f"Attachment exceeds the {max_bytes}-byte cap.",
            hint="Raise max_attachment_bytes in the config file if you really need it.",
        )

    def _is_allowed_download_url(self, url: str) -> bool:
        """The OAuth token rides on this request, so it may only go to https Zoho hosts of the
        configured data center, whatever an attachment record claims its download URL is."""
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
        except ValueError:
            return False
        if parsed.scheme != "https" or not host:
            return False
        dc = self._settings.dc
        exact = (f"zoho.{dc}", f"zohopublic.{dc}")
        return host in exact or host.endswith(tuple(f".{h}" for h in exact))

    def download(self, url: str, max_bytes: int | None = None) -> tuple[bytes, str]:
        """GET a file (e.g. an attachment) through the same guard, limiter and retry as API calls.
        Returns `(bytes, content_type)`; a body over `max_bytes` (default: the configured attachment
        cap) is an error, never a truncated result."""
        if not self._is_allowed_download_url(url):
            try:
                host = urlparse(url).hostname or "(none)"
            except ValueError:
                host = "(unparseable)"
            raise UpstreamError(
                "download.host_refused",
                f"Refusing to download from {host!r}: not an https Zoho host for data center `{self._settings.dc}`.",
                hint="The OAuth token is only ever sent to Zoho hosts.",
            )
        cap = max_bytes or self._settings.max_attachment_bytes
        response = self._check(self._request(url, None, "attachment download", cap), "attachment download")
        if len(response.body) > cap:
            raise self._too_large(cap)
        return response.body, response.content_type

    # --- OAuth token refresh (used by ZohoAuth) ---

    def token_post(self, url: str, data: Mapping[str, str], timeout: float) -> dict:
        """`ZohoAuth`'s `post`: the only POST the gateway will ever send, and only to the token
        endpoint. Goes through the limiter and 429 retry like everything else. Network failures
        raise OSError (as ZohoAuth expects); a non-JSON reply becomes an `{"error": ...}` body."""
        response = self._dispatch("POST", url, {}, None, timeout, data=data)
        try:
            body = json.loads(response.body)
        except ValueError:
            body = None
        return body if isinstance(body, dict) else {"error": f"non-JSON response (HTTP {response.status})"}
