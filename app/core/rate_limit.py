"""Small in-process sliding-window rate limiter for auth endpoints.

State is per process: with several API workers/instances each one counts
separately. That is acceptable abuse protection for the MVP; move to a shared
store (e.g. Redis or a database table) if the API is scaled out.
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock

from fastapi import HTTPException, status


class RateLimiter:
    def __init__(self, max_hits: int, window_seconds: int) -> None:
        self.max_hits = max_hits
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window_seconds:
                hits.popleft()
            if len(hits) >= self.max_hits:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many attempts. Please wait and try again.",
                )
            hits.append(now)
            if len(self._hits) > 10_000:
                self._prune(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window_seconds]:
            del self._hits[key]


# Per account: slows password guessing and email flooding of a single inbox.
login_by_email = RateLimiter(max_hits=10, window_seconds=15 * 60)
email_send_by_email = RateLimiter(max_hits=5, window_seconds=60 * 60)
# Per client IP: generous, because many users can share one carrier-grade NAT address.
auth_by_ip = RateLimiter(max_hits=60, window_seconds=15 * 60)

ALL_LIMITERS = (login_by_email, email_send_by_email, auth_by_ip)
