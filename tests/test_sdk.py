"""M7 — the SDK, driven against the real API.

Every test here goes through the actual REST surface behind the actual auth
middleware. An SDK tested against a mock proves only that the mock agrees with it.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from starlette.routing import Route

from coletar.mcp import rest
from coletar.mcp.auth import ApiKeyAuthenticator, AuthMiddleware
from coletar.mcp.ratelimit import RateLimiter
from coletar.schema.objects import MemoryKind
from coletar.schema.tenancy import tenant_id
from coletar.sdk import Coletar, ColetarError, NotFound, RateLimited, Unauthorized
from coletar.store.memory import InMemoryStore

TENANT = tenant_id("tenant_sdk")
KEYS = json.dumps(
    [
        {"id": "writer", "secret": "sk-write", "tenant_id": str(TENANT)},
        {"id": "reader", "secret": "sk-read", "tenant_id": str(TENANT), "scopes": ["read"]},
    ]
)


@pytest.fixture
def api(monkeypatch):
    """The real routes, the real middleware, one in-process store."""
    store = InMemoryStore()
    monkeypatch.setattr(rest, "build_store", lambda: store)

    app = FastAPI()
    for path, endpoint, methods in rest.routes():
        app.router.routes.append(Route(path, endpoint, methods=methods))
    wrapped = AuthMiddleware(
        app,
        ApiKeyAuthenticator.from_config(KEYS),
        rate_limiter=RateLimiter(requests_per_minute=6000, burst=500),
    )
    transport = httpx.ASGITransport(app=wrapped)
    return store, transport


def client_for(transport, secret: str = "sk-write") -> Coletar:
    return Coletar(
        "http://testserver",
        api_key=secret,
        client=httpx.AsyncClient(transport=transport, base_url="http://testserver"),
    )


# --- the guarantee that is a property, not a promise -----------------------------


def test_the_sdk_exposes_no_hard_delete() -> None:
    """Constraint 6 in the shape of an API. `retire` is the closest thing and it
    deliberately is not one — a convenience method that removed a row would turn a
    guarantee into a convention, and conventions get worked around."""
    public = {name for name in dir(Coletar) if not name.startswith("_")}
    assert not {n for n in public if "delete" in n or "purge" in n or "destroy" in n}
    assert {"remember", "search", "inspect", "history", "supersede", "retire", "compile"} <= public


def test_no_endpoint_accepts_the_delete_verb() -> None:
    """The property underneath it: there is nothing for a delete method to call."""
    for _, _, methods in rest.routes():
        assert "DELETE" not in methods


def test_the_sdk_talks_to_one_host_and_sends_no_telemetry() -> None:
    """Not "redacted by default" — none. A claim a test can check, where a privacy
    policy cannot."""
    source = inspect.getsource(Coletar)
    assert "self.base_url" in source
    # Every request goes through one method, so there is a single place a second
    # destination could ever appear.
    assert source.count("self._client.request") == 1
    assert not any(
        marker in source.lower()
        for marker in ("analytics", "telemetry", "segment.io", "posthog", "sentry")
    )


# --- the surface, end to end ------------------------------------------------------


@pytest.mark.asyncio
async def test_remember_then_search_then_inspect(api) -> None:
    store, transport = api
    async with client_for(transport) as client:
        written = await client.remember(
            "I prefer fixed-point integers over doubles for money",
            kind=str(MemoryKind.PREFERENCE),
        )
        object_id = written["object_id"]

        hits = await client.search("how should I represent money")
        assert any(hit["id"] == object_id for hit in hits)

        obj = await client.inspect(object_id)
        assert "fixed-point" in obj["content"]
        # Provenance survives the round trip; §4 says an object we cannot explain
        # should not exist.
        assert obj["provenance"]["provider"]


@pytest.mark.asyncio
async def test_search_can_explain_itself(api) -> None:
    """A ranked list you cannot interrogate is one you have to trust."""
    store, transport = api
    async with client_for(transport) as client:
        await client.remember("Chris prefers tabs over spaces")
        hits = await client.search("tabs", explain=True)
        assert hits
        assert "components" in hits[0] or "score" in hits[0]


@pytest.mark.asyncio
async def test_supersede_leaves_the_old_object_readable(api) -> None:
    """Constraint 6. The correction replaces it in retrieval; history still answers."""
    store, transport = api
    async with client_for(transport) as client:
        first = await client.remember("Chris works at Acme Corp")
        old_id = first["object_id"]

        await client.supersede(old_id, "Chris works at Globex")

        assert (await client.inspect(old_id))["content"] == "Chris works at Acme Corp"
        found = [hit["content"] for hit in await client.search("where does Chris work")]
        assert "Chris works at Acme Corp" not in found


@pytest.mark.asyncio
async def test_history_shows_what_an_object_used_to_say(api) -> None:
    store, transport = api
    async with client_for(transport) as client:
        written = await client.remember("Chris prefers tabs")
        revisions = await client.history(written["object_id"])
        assert revisions
        assert revisions[0]["after"]["content"] == "Chris prefers tabs"


@pytest.mark.asyncio
async def test_retire_hides_an_object_without_removing_it(api) -> None:
    store, transport = api
    async with client_for(transport) as client:
        written = await client.remember("An old note about nothing")
        object_id = written["object_id"]

        result = await client.retire(object_id, reason="no longer true")
        assert result["retired"] and result["readable"]

        assert await client.inspect(object_id)          # still there
        assert not await client.search("old note about nothing")


@pytest.mark.asyncio
async def test_retiring_without_a_reason_is_refused(api) -> None:
    """A retirement nobody can explain later is indistinguishable from a bug."""
    store, transport = api
    async with client_for(transport) as client:
        written = await client.remember("Something")
        with pytest.raises(ColetarError):
            await client.retire(written["object_id"], reason="   ")


# --- the rules the SDK cannot reach past -------------------------------------------


@pytest.mark.asyncio
async def test_a_read_only_key_cannot_write(api) -> None:
    store, transport = api
    async with client_for(transport, "sk-read") as client:
        assert await client.search("anything") == []
        with pytest.raises(Unauthorized):
            await client.remember("should not land")


@pytest.mark.asyncio
async def test_the_tenant_comes_from_the_key_not_the_caller(api) -> None:
    """There is no tenant parameter anywhere in the SDK, which is what stops a client
    naming someone else's graph."""
    store, transport = api
    async with client_for(transport) as client:
        await client.remember("Chris prefers tabs")
    assert await store.list_objects(TENANT, limit=10)
    assert await store.list_objects(tenant_id("tenant_someone_else"), limit=10) == []

    assert "tenant" not in inspect.signature(Coletar.remember).parameters
    assert "tenant" not in inspect.signature(Coletar.search).parameters


@pytest.mark.asyncio
async def test_compile_is_gated_on_review_exactly_as_the_cli_is(api) -> None:
    """An API that could walk around the gate would make it a UI courtesy."""
    store, transport = api
    async with client_for(transport) as client:
        await client.remember("Chris prefers tabs")
        with pytest.raises(ColetarError) as caught:
            await client.compile(destination="local")
        assert caught.value.status == 409
        assert "reviewed" in str(caught.value)


@pytest.mark.asyncio
async def test_an_unknown_object_is_not_found(api) -> None:
    store, transport = api
    async with client_for(transport) as client:
        with pytest.raises(NotFound):
            await client.inspect("mem_does_not_exist")


@pytest.mark.asyncio
async def test_rate_limiting_surfaces_as_a_typed_error_with_a_wait(api) -> None:
    store, transport = api
    app = AuthMiddleware(
        _routed_app(),
        ApiKeyAuthenticator.from_config(KEYS),
        rate_limiter=RateLimiter(requests_per_minute=60, burst=1),
    )
    limited = httpx.ASGITransport(app=app)
    async with client_for(limited) as client:
        with pytest.raises((RateLimited, NotFound)):
            for _ in range(5):
                await client.inspect("mem_anything")


def _routed_app() -> FastAPI:
    app = FastAPI()
    for path, endpoint, methods in rest.routes():
        app.router.routes.append(Route(path, endpoint, methods=methods))
    return app


def test_an_sdk_without_a_key_refuses_to_construct() -> None:
    with pytest.raises(ColetarError, match="anonymous"):
        Coletar("http://testserver", api_key="")


# --- the two SDKs describe one API -----------------------------------------------

JS_SDK = Path(__file__).resolve().parents[1] / "sdk" / "js" / "index.mjs"


def _js_methods() -> set[str]:
    """Public async methods on the JS class, read from source.

    Parsing rather than executing: this suite should not need a Node toolchain to
    notice the two clients disagreeing.
    """
    source = JS_SDK.read_text()
    body = source[source.index("export class Coletar"):]
    return {
        match.group(1)
        for match in re.finditer(r"^  async (\w+)\(", body, re.MULTILINE)
        if not match.group(1).startswith("_")
    }


def test_the_python_and_js_sdks_expose_the_same_surface() -> None:
    """Two SDKs that drift are two descriptions of one API, and the second one is
    always the one that lies. Drift fails here rather than being discovered by
    whoever happened to pick the other language."""
    python = {
        name
        for name, value in vars(Coletar).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(value)
    } - {"aclose"}
    assert python == _js_methods()


def test_neither_sdk_grew_a_delete() -> None:
    """Checked as a *verb*, not as a word. Grepping the source for "delete" trips on
    the comment explaining there isn't one, which is how a documented guarantee ends
    up failing its own test."""
    assert not {name for name in _js_methods() if "delete" in name or "purge" in name}
    assert 'method: "DELETE"' not in JS_SDK.read_text()
    assert '"DELETE"' not in JS_SDK.read_text()


def test_the_js_sdk_has_no_dependencies() -> None:
    """A dependency has to survive being said out loud, and a client that wraps
    `fetch` does not need one."""
    manifest = json.loads((JS_SDK.parent / "package.json").read_text())
    assert "dependencies" not in manifest
    assert "peerDependencies" not in manifest
