"""Bearer-token gate in front of the whole app."""
from __future__ import annotations

import hmac
import json


def bearer(headers) -> str | None:
    if not headers:
        return None
    value = None
    for k, v in dict(headers).items():
        if str(k).lower() == "authorization":
            value = v
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    if not value or not isinstance(value, str) or not value.lower().startswith("bearer "):
        return None
    return value[7:].strip() or None


def _bytes(token: str) -> bytes:
    # hmac.compare_digest on str raises TypeError for non-ASCII input, which would
    # turn a bad header into a 500 instead of a 401; compare bytes instead.
    return token.encode("utf-8", "surrogatepass")


class BearerAuth:
    def __init__(self, app, runner_token: str, store):
        if not runner_token:
            raise ValueError("a runner token is required")
        self.app, self.runner_token, self.store = app, runner_token, store
        self._runner_bytes = _bytes(runner_token)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        token = bearer(headers)
        if path == "/health":
            ok = True
        elif path.startswith("/runner/") or path == "/runner":
            ok = token is not None and hmac.compare_digest(_bytes(token), self._runner_bytes)
        elif path.startswith("/agent/") or path == "/agent":
            ok = token is not None and self.store.item_for_token(token) is not None
        else:
            return await _respond(send, 404, {"error": "not found"})
        if not ok:
            return await _respond(send, 401, {"error": "unauthorized"})
        return await self.app(scope, receive, send)


async def _respond(send, status, body):
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(data)).encode())]})
    await send({"type": "http.response.body", "body": data})
