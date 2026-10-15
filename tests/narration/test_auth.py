import asyncio
import contextlib

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from narration.auth import BearerAuth, bearer
from narration.workitems import WorkItemStore


def _app(tmp_path):
    store = WorkItemStore(tmp_path)
    ok = lambda request: PlainTextResponse("ok")
    inner = Starlette(routes=[Route("/health", ok), Route("/runner/x", ok), Route("/agent/x", ok)])
    return TestClient(BearerAuth(inner, "runner-secret", store)), store


def test_health_is_open(tmp_path):
    client, _ = _app(tmp_path)
    assert client.get("/health").status_code == 200


def test_runner_needs_the_runner_token(tmp_path):
    client, store = _app(tmp_path)
    _, item_token = store.open("x", {})
    assert client.get("/runner/x").status_code == 401
    assert client.get("/runner/x", headers={"Authorization": f"Bearer {item_token}"}).status_code == 401
    assert client.get("/runner/x", headers={"Authorization": "Bearer runner-secret"}).status_code == 200


def test_agent_needs_a_live_item_token(tmp_path):
    client, store = _app(tmp_path)
    item, token = store.open("x", {})
    assert client.get("/agent/x", headers={"Authorization": "Bearer runner-secret"}).status_code == 401
    assert client.get("/agent/x", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    store.revoke(item)
    assert client.get("/agent/x", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_unknown_paths_are_404(tmp_path):
    client, _ = _app(tmp_path)
    assert client.get("/other", headers={"Authorization": "Bearer runner-secret"}).status_code == 404


def test_bearer_parsing():
    assert bearer({"authorization": "Bearer abc"}) == "abc"
    assert bearer({"Authorization": "bearer abc"}) == "abc"
    assert bearer({"authorization": "Basic abc"}) is None
    assert bearer({}) is None and bearer(None) is None


def test_bearer_without_a_token_is_none():
    assert bearer({"authorization": "Bearer"}) is None
    assert bearer({"authorization": "Bearer "}) is None
    assert bearer({"authorization": "Bearer    "}) is None
    assert bearer({"authorization": ""}) is None


def test_bearer_takes_a_starlette_headers_object():
    from starlette.datastructures import Headers

    assert bearer(Headers({"Authorization": "Bearer abc"})) == "abc"


def test_failures_are_401_json(tmp_path):
    client, _ = _app(tmp_path)
    for path in ("/runner/x", "/agent/x"):
        r = client.get(path)
        assert r.status_code == 401
        assert r.headers["content-type"] == "application/json"
        assert r.json() == {"error": "unauthorized"}
        assert int(r.headers["content-length"]) == len(r.content)


def test_unknown_path_is_404_json_even_without_a_token(tmp_path):
    client, _ = _app(tmp_path)
    r = client.get("/other")
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/json"
    assert r.json() == {"error": "not found"}


def test_a_non_ascii_token_is_401_not_500(tmp_path):
    # hmac.compare_digest on str raises TypeError for non-ASCII; it must compare bytes.
    client, _ = _app(tmp_path)
    for token in ("ü", "runner-secrét", "日本語"):
        for path in ("/runner/x", "/agent/x"):
            r = client.get(path, headers={"Authorization": f"Bearer {token}".encode("utf-8")})
            assert r.status_code == 401, (path, token)


def test_header_name_case_and_scheme_case_are_tolerated(tmp_path):
    client, _ = _app(tmp_path)
    for name in ("authorization", "AUTHORIZATION", "Authorization"):
        assert client.get("/runner/x", headers={name: "bearer runner-secret"}).status_code == 200
        assert client.get("/runner/x", headers={name: "BEARER runner-secret"}).status_code == 200


def test_malformed_headers_are_401(tmp_path):
    client, store = _app(tmp_path)
    _, token = store.open("x", {})
    for value in ("Bearer", "Bearer ", "Basic runner-secret", "runner-secret",
                  "Bearer runner-secret extra", "Bearer runner secret", "Bearer\trunner-secret x"):
        assert client.get("/runner/x", headers={"Authorization": value}).status_code == 401, value
    for value in ("Bearer", "Bearer ", f"Token {token}", token, f"Bearer {token} extra"):
        assert client.get("/agent/x", headers={"Authorization": value}).status_code == 401, value


def test_path_prefixes_are_exact(tmp_path):
    client, _ = _app(tmp_path)
    auth = {"Authorization": "Bearer runner-secret"}
    for path in ("/runners/x", "/runnerx", "/agents/x", "/agentx", "/healthz", "/health/x", "/", "/Runner/x"):
        assert client.get(path, headers=auth).status_code == 404, path


def test_asgi_header_list_of_bytes_is_read():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    class Store:
        def item_for_token(self, token):
            return "a1b2c3d4e5f6" if token == "item-token" else None

    app = BearerAuth(inner, "runner-secret", Store())

    def call(path, headers):
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request"}

        asyncio.run(app({"type": "http", "path": path, "headers": headers}, receive, send))
        return sent[0]["status"]

    assert call("/runner/x", [(b"authorization", b"Bearer runner-secret")]) == 200
    assert call("/agent/x", [(b"Authorization", b"Bearer item-token")]) == 200
    assert call("/agent/x", [(b"authorization", b"Bearer \xfc")]) == 401  # latin-1 u-umlaut
    assert call("/agent/x", []) == 401
    assert call("/agent", [(b"authorization", b"Bearer item-token")]) == 200
    assert call("/runner", [(b"authorization", b"Bearer nope")]) == 401
    assert seen == ["/runner/x", "/agent/x", "/agent"]


def test_the_runner_token_is_required(tmp_path):
    store = WorkItemStore(tmp_path)
    for token in ("", None):
        try:
            BearerAuth(lambda *a: None, token, store)
        except ValueError:
            continue
        raise AssertionError("an empty runner token must be refused")


def test_non_http_scopes_pass_through_untouched(tmp_path):
    store = WorkItemStore(tmp_path)
    got = []

    async def inner(scope, receive, send):
        got.append((scope["type"], scope, receive, send))

    app = BearerAuth(inner, "runner-secret", store)
    for kind in ("lifespan", "websocket"):
        scope = {"type": kind, "path": "/anything"}
        receive, send = object(), object()
        asyncio.run(app(scope, receive, send))
        assert got[-1][0] == kind
        assert got[-1][1] is scope and got[-1][2] is receive and got[-1][3] is send


def test_lifespan_reaches_the_wrapped_app(tmp_path):
    store = WorkItemStore(tmp_path)
    events = []

    @contextlib.asynccontextmanager
    async def lifespan(app):
        events.append("start")
        yield
        events.append("stop")

    inner = Starlette(routes=[Route("/health", lambda request: PlainTextResponse("ok"))], lifespan=lifespan)
    with TestClient(BearerAuth(inner, "runner-secret", store)) as client:
        assert events == ["start"]
        assert client.get("/health").status_code == 200
    assert events == ["start", "stop"]
