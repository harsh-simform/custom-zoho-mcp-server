"""OAuth access-token handling. The access token lives in memory only, with its expiry;
nothing here writes to disk, and no credential or token is ever logged or put in an error."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .config import Settings
from .errors import AuthError, RateLimitedError, UpstreamError

logger = logging.getLogger(__name__)

TOKEN_TIMEOUT_SECONDS = 20
EXPIRY_MARGIN_SECONDS = 60

# (url, form data, timeout) -> parsed JSON body; raises OSError when Zoho cannot be reached and
# RateLimitedError on HTTP 429. By default this is the gateway's `token_post`, so the refresh goes
# through the same guard, limiter, retry and transport as every other request; tests inject a fake.
PostFn = Callable[[str, dict, float], dict]


def _is_throttle(body: object) -> bool:
    """Zoho answers token throttling with an error body ("too many requests ...") rather than a 429."""
    if not isinstance(body, dict):
        return False
    text = " ".join(str(body.get(k, "")) for k in ("error", "error_description", "message")).lower()
    return "too many requests" in text


class ZohoAuth:
    def __init__(self, settings: Settings, post: PostFn | None = None, clock: Callable[[], float] = time.time):
        self._settings = settings
        if post is None:
            from .gateway import ZohoGateway  # deferred: the gateway imports this module

            post = ZohoGateway(settings).token_post
        self._post = post
        self._clock = clock
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        client_id, client_secret, refresh_token = self._settings.require_credentials()
        logger.info("Refreshing Zoho access token")
        try:
            body = self._post(
                f"{self._settings.accounts_base}/oauth/v2/token",
                {
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                },
                TOKEN_TIMEOUT_SECONDS,
            )
        except OSError as exc:  # requests' exceptions, timeouts and connection errors all derive from OSError
            raise UpstreamError(
                "upstream.unreachable",
                f"Could not reach {self._settings.accounts_base}: {type(exc).__name__}",
                hint="Check your network connection and the `dc` (data center) setting, then retry.",
            ) from None

        token = body.get("access_token") if isinstance(body, dict) else None
        if not token and _is_throttle(body):
            logger.error("OAuth refresh throttled by Zoho")
            raise RateLimitedError(
                "rate_limited.token",
                "Zoho is throttling token refreshes (too many requests).",
                hint="Wait a few minutes before retrying; repeated refreshes extend the lockout.",
            )
        if not token:
            reason = str(body.get("error", "unknown error")) if isinstance(body, dict) else "unexpected response"
            logger.error("OAuth refresh failed: %s", reason)
            raise AuthError(
                "auth.refresh_failed",
                f"Zoho rejected the token refresh: {reason}",
                hint=(
                    "Check client_id, client_secret and refresh_token, and that `dc` matches your account's region. "
                    "If Zoho mentions too many requests, wait a few minutes and retry."
                ),
            )
        lifetime = body.get("expires_in")
        if isinstance(lifetime, bool) or not isinstance(lifetime, (int, float)) or lifetime <= 0:
            lifetime = 3600
        self._access_token = token
        self._expires_at = self._clock() + lifetime - min(EXPIRY_MARGIN_SECONDS, lifetime / 2)
        logger.info("Access token refreshed")

    def get_access_token(self) -> str:
        with self._lock:
            if not self._access_token or self._clock() >= self._expires_at:
                self._refresh()
            return self._access_token  # type: ignore[return-value]

    def invalidate(self) -> None:
        """Forget the cached access token (Zoho answered 401 to it): the next `get_access_token` refreshes."""
        with self._lock:
            self._access_token = None
            self._expires_at = 0.0

    def validate(self) -> None:
        with self._lock:
            self._refresh()
