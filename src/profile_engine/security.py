from __future__ import annotations

import hmac
import re
import time
from collections import defaultdict, deque
from math import ceil
from threading import Lock


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def constant_time_equal(value: str, expected: str) -> bool:
    """Compare untrusted Unicode credentials without compare_digest TypeError."""
    return hmac.compare_digest(value.encode("utf-8"), expected.encode("utf-8"))


def safe_request_id(value: str | None, fallback: str) -> str:
    candidate = (value or "").strip()
    return candidate if REQUEST_ID_PATTERN.fullmatch(candidate) else fallback


class SlidingWindowRateLimiter:
    def __init__(self, max_keys: int = 10_000) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_seen: dict[str, float] = {}
        self._max_keys = max_keys
        self._lock = Lock()

    def _prune_keys(self, cutoff: float) -> None:
        stale = [key for key, seen in self._last_seen.items() if seen <= cutoff]
        for key in stale:
            self._hits.pop(key, None)
            self._last_seen.pop(key, None)
        if len(self._hits) < self._max_keys:
            return
        for key, _ in sorted(self._last_seen.items(), key=lambda item: item[1])[:max(1, self._max_keys // 10)]:
            self._hits.pop(key, None)
            self._last_seen.pop(key, None)

    def check(self, key: str, limit: int, now: float | None = None) -> tuple[bool, int, int]:
        current = time.monotonic() if now is None else now
        cutoff = current - 60
        with self._lock:
            if key not in self._hits and len(self._hits) >= self._max_keys:
                self._prune_keys(cutoff)
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            self._last_seen[key] = current
            if len(hits) >= limit:
                retry_after = max(1, ceil(60 - (current - hits[0])))
                return False, 0, retry_after
            hits.append(current)
            return True, max(0, limit - len(hits)), 0
