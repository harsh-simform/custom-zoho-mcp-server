"""Client-side sliding-window request limiter. Zoho allows roughly 100 requests per 2 minutes and
locks the API for 30 minutes on a breach, so we refuse early instead of risking that."""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from .errors import RateLimitedError

DEFAULT_LIMIT = 100
DEFAULT_WINDOW_SECONDS = 120.0
DEFAULT_MAX_WAIT_SECONDS = 2.0


class RateLimiter:
    def __init__(
        self,
        limit: int = DEFAULT_LIMIT,
        window: float = DEFAULT_WINDOW_SECONDS,
        max_wait: float = DEFAULT_MAX_WAIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._limit, self._window, self._max_wait = limit, window, max_wait
        self._clock, self._sleep = clock, sleep
        self._stamps: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._stamps and self._stamps[0] <= now - self._window:
            self._stamps.popleft()

    def acquire(self) -> None:
        """Take a slot, sleeping at most `max_wait` seconds for one to free up; otherwise fail fast."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._stamps) >= self._limit:
                wait = self._stamps[0] + self._window - now
                if wait > self._max_wait:
                    raise RateLimitedError(
                        "rate_limited.client",
                        f"Client-side request limit reached ({self._limit} requests per {self._window:g}s).",
                        hint=(
                            f"Wait about {int(wait) + 1}s and retry; exceeding Zoho's limit locks the API "
                            "for 30 minutes. Cached or narrower requests use fewer calls."
                        ),
                    )
                self._sleep(wait)
                now = self._clock()
                self._prune(now)
            self._stamps.append(now)
