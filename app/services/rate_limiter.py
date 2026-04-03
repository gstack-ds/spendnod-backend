"""Sliding-window in-memory rate limiter for the authorize endpoint."""

import time
from collections import defaultdict

from fastapi import HTTPException, status

from app.config import settings


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._window: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        """Raise HTTP 429 if key has exceeded the rate limit, otherwise record the call."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        calls = [t for t in self._window[key] if t > cutoff]
        if len(calls) >= self.max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded: {self.max_requests} requests "
                    f"per {self.window_seconds}s per agent"
                ),
            )
        calls.append(now)
        self._window[key] = calls

    def reset(self, key: str | None = None) -> None:
        """Clear rate limit state — for use in tests."""
        if key is None:
            self._window.clear()
        else:
            self._window.pop(key, None)


# Module-level singleton used by the authorize endpoint
authorize_limiter = RateLimiter(
    max_requests=settings.RATE_LIMIT_REQUESTS,
    window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
)
