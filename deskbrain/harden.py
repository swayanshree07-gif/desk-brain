"""Day 26: hardening — security headers on every response and small rate limits.

- ``SecurityHeadersMiddleware`` adds nosniff, frame-deny, no-referrer, a strict
  same-origin CSP and (for /api/ and /pair) ``Cache-Control: no-store`` to every
  HTTP response, including 4xx answers from inner middleware.
- ``RateLimiter`` is a per-key sliding window (in memory, thread-safe). Used for
  ``/api/ask`` (per client) and for failed LAN token attempts (per client IP).
"""
from __future__ import annotations

import threading
import time
from collections import deque

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")

BASE_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", CSP.encode()),
]


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        no_store = path.startswith("/api/") or path == "/pair"

        async def wrapped(msg):
            if msg["type"] == "http.response.start":
                have = {k.lower() for k, _ in msg.get("headers", [])}
                extra = [(k, v) for k, v in BASE_HEADERS if k not in have]
                if no_store and b"cache-control" not in have:
                    extra.append((b"cache-control", b"no-store"))
                msg = dict(msg, headers=list(msg.get("headers", [])) + extra)
            await send(msg)

        await self.app(scope, receive, wrapped)


class RateLimiter:
    """At most ``limit`` events per ``window`` seconds per key."""

    def __init__(self, limit: int, window: float, clock=time.monotonic, max_keys: int = 4096):
        if limit < 1 or window <= 0:
            raise ValueError("bad rate limit")
        self.limit, self.window, self.clock, self.max_keys = limit, window, clock, max_keys
        self._ev: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> deque:
        q = self._ev.get(key)
        if q is None:
            if len(self._ev) >= self.max_keys:   # bound memory: drop stale keys, then oldest
                for k in [k for k, d in self._ev.items() if not d or d[-1] <= now - self.window]:
                    del self._ev[k]
                if len(self._ev) >= self.max_keys:
                    del self._ev[next(iter(self._ev))]
            q = self._ev[key] = deque()
        while q and q[0] <= now - self.window:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        """True if ``key`` has used up its budget (does not record an event)."""
        with self._lock:
            return len(self._trim(key, self.clock())) >= self.limit

    def hit(self, key: str) -> bool:
        """Record an event; return False (and don't record) if over the limit."""
        with self._lock:
            now = self.clock()
            q = self._trim(key, now)
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def retry_after(self, key: str) -> int:
        with self._lock:
            q = self._trim(key, self.clock())
            return max(1, int(q[0] + self.window - self.clock() + 0.999)) if q else 1
