"""In-process rate limiters.

State lives in this process, so with several gateway replicas each replica enforces its
own limit (the effective limit is replicas x limit). That is a deliberate simplification;
a shared store such as Redis would make the limit global.
"""

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    """Allow at most ``limit`` events per ``window`` seconds for each key."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, events: deque[float], now: float) -> None:
        while events and events[0] <= now - self._window:
            events.popleft()

    def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds) without recording an event."""
        if self._limit <= 0:
            return True, 0
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            self._prune(events, now)
            if len(events) < self._limit:
                return True, 0
            return False, max(1, int(events[0] + self._window - now) + 1)

    def hit(self, key: str) -> tuple[bool, int]:
        """Record an event if allowed. Returns (allowed, retry_after_seconds)."""
        if self._limit <= 0:
            return True, 0
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            self._prune(events, now)
            if len(events) >= self._limit:
                return False, max(1, int(events[0] + self._window - now) + 1)
            events.append(now)
            return True, 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def sweep(self) -> None:
        """Drop keys with no recent events so the table cannot grow without bound."""
        now = time.monotonic()
        with self._lock:
            for key in list(self._events):
                self._prune(self._events[key], now)
                if not self._events[key]:
                    del self._events[key]
