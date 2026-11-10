"""Day 24: LAN mode part 2 — token auth on every route.

Pure ASGI middleware, only installed when LAN mode is on. Rules:
- Requests arriving on a loopback *server socket* (the local address the
  connection came in on, not a client-supplied header) need no token.
- Everything else needs the token: ``Authorization: Bearer <t>``,
  ``X-Desk-Token: <t>``, or the HttpOnly cookie. A ``?token=<t>`` in the URL
  of a GET is accepted once and answered with a 303 to the same URL minus the
  token, setting the cookie (so the token does not stay in history/logs).
- The cookie holds an HMAC of the token, not the token itself.
- All compares are constant-time (``hmac.compare_digest``).
- Failures get a bare 401 with no desk content.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
from urllib.parse import parse_qsl, urlencode

COOKIE = "deskbrain_auth"


def cookie_value(token: str) -> str:
    return hmac.new(token.encode(), b"deskbrain-cookie-v1", hashlib.sha256).hexdigest()


def _is_loopback(host) -> bool:
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8", "replace"), b.encode("utf-8", "replace"))


class TokenAuthMiddleware:
    def __init__(self, app, token: str, fail_limiter=None):
        if not token or len(token) < 20:
            raise ValueError("LAN token too short")
        self.app = app
        self.fails = fail_limiter   # Day 26: RateLimiter of failed attempts per client IP
        self.token = token
        self.cookie = cookie_value(token)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        server = scope.get("server") or (None, None)
        if _is_loopback(server[0]):
            return await self.app(scope, receive, send)

        client = str((scope.get("client") or ("?",))[0])
        if self.fails is not None and self.fails.blocked(client):
            return await self._deny(scope, send, 429, b"429 too many failed token attempts; wait a minute\n",
                                    [(b"retry-after", str(self.fails.retry_after(client)).encode())])

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and _eq(auth[7:].strip(), self.token):
            return await self.app(scope, receive, send)
        if "x-desk-token" in headers and _eq(headers["x-desk-token"].strip(), self.token):
            return await self.app(scope, receive, send)
        for part in headers.get("cookie", "").split(";"):
            name, _, val = part.strip().partition("=")
            if name == COOKIE and _eq(val, self.cookie):
                return await self.app(scope, receive, send)

        qs = scope.get("query_string", b"").decode("latin-1")
        pairs = parse_qsl(qs, keep_blank_values=True)
        given = [v for k, v in pairs if k == "token"]
        if scope["type"] == "http" and scope.get("method") == "GET" and len(given) == 1 \
                and _eq(given[0], self.token):
            rest = urlencode([(k, v) for k, v in pairs if k != "token"])
            loc = scope.get("path", "/") + (f"?{rest}" if rest else "")
            if not loc.startswith("/") or loc.startswith("//"):
                loc = "/"
            await send({"type": "http.response.start", "status": 303, "headers": [
                (b"location", loc.encode("latin-1")),
                (b"set-cookie", f"{COOKIE}={self.cookie}; HttpOnly; SameSite=Strict; Path=/".encode()),
                (b"cache-control", b"no-store"),
                (b"referrer-policy", b"no-referrer"),
                (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return

        # Only count real guesses (a wrong token/cookie was presented), not plain visits.
        if self.fails is not None and (auth or "x-desk-token" in headers or given
                                       or COOKIE in headers.get("cookie", "")):
            self.fails.hit(client)
        await self._deny(scope, send, 401, b"401 unauthorized: LAN mode needs the token\n",
                         [(b"www-authenticate", b"Bearer")])

    @staticmethod
    async def _deny(scope, send, status: int, body: bytes, extra: list) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"cache-control", b"no-store"),
            (b"content-length", str(len(body)).encode())] + extra})
        await send({"type": "http.response.body", "body": body})
