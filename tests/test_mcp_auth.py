"""M2.1: the auth layer in front of the MCP server.

The build plan's criterion is one line — "unauthenticated requests are rejected" —
but the failure modes worth pinning are the ones where that line is technically true
and practically false: an exemption list that grew, a server that starts with no keys
and lets everything through, a key whose scope is checked in one tool and not another.
"""

from __future__ import annotations

import json

import httpx
import pytest

from coletar.mcp.auth import (
    EXEMPT_PATHS,
    SCOPE_READ,
    SCOPE_WRITE,
    ApiKeyAuthenticator,
    AuthError,
    AuthMiddleware,
    Principal,
    bearer_token,
    current_principal,
    principal_scope,
)
from conftest import TENANT


async def _ok_app(scope, receive, send):
    """Minimal inner app: proves the request reached past the gate."""
    body = current_principal().id.encode() if current_principal() else b"anonymous"
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _client(authenticator: ApiKeyAuthenticator) -> httpx.AsyncClient:
    app = AuthMiddleware(_ok_app, authenticator)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


# -- key parsing --------------------------------------------------------------
def _keys(*entries: dict) -> str:
    return json.dumps(list(entries))


ALICE_KEY = _keys({"id": "alice", "secret": "sk-alice", "tenant_id": "tenant_alice"})


def test_keys_parse_with_and_without_explicit_scopes():
    auth = ApiKeyAuthenticator.from_config(
        _keys(
            {"id": "alice", "secret": "sk-alice", "tenant_id": "tenant_alice"},
            {"id": "dash", "secret": "sk-dash", "tenant_id": "tenant_alice",
             "scopes": ["read"]},
        )
    )
    assert len(auth) == 2

    alice = auth.authenticate("sk-alice")
    dashboard = auth.authenticate("sk-dash")

    assert alice is not None and alice.can(SCOPE_WRITE) and alice.can(SCOPE_READ)
    assert dashboard is not None and dashboard.can(SCOPE_READ)
    assert not dashboard.can(SCOPE_WRITE)


def test_every_principal_belongs_to_exactly_one_tenant():
    """The tenant comes from the key and from nowhere else — there is no
    configuration fallback reachable from the MCP server."""
    auth = ApiKeyAuthenticator.from_config(
        _keys(
            {"id": "alice", "secret": "sk-alice", "tenant_id": "tenant_alice"},
            {"id": "bob", "secret": "sk-bob", "tenant_id": "tenant_bob"},
        )
    )
    assert auth.authenticate("sk-alice").tenant_id == "tenant_alice"
    assert auth.authenticate("sk-bob").tenant_id == "tenant_bob"
    assert auth.tenants == {"tenant_alice", "tenant_bob"}


def test_empty_configuration_yields_no_keys():
    """Which means the middleware rejects everything — fail closed, not fail open."""
    assert len(ApiKeyAuthenticator.from_config("")) == 0
    assert len(ApiKeyAuthenticator.from_config("   ")) == 0
    assert len(ApiKeyAuthenticator.from_config("[]")) == 0


@pytest.mark.parametrize(
    "raw",
    [
        "alice:sk-alice",  # the old colon-delimited form is no longer accepted
        "{}",
        "[1, 2]",
        '[{"id": "a", "secret": "s"}]',                              # no tenant
        '[{"id": "a", "tenant_id": "tenant_a"}]',                    # no secret
        '[{"secret": "s", "tenant_id": "tenant_a"}]',                # no id
        '[{"id": "a", "secret": "s", "tenant_id": "UPPER"}]',        # invalid tenant
        '[{"id": "a", "secret": "s", "tenant_id": "tenant_a", "scopes": ["admin"]}]',
        "not json at all",
    ],
)
def test_malformed_key_configuration_is_refused_at_startup(raw: str):
    """A misconfigured server should fail to boot rather than fail closed silently on
    every call — and a key entry whose tenant is wrong reaches the wrong graph."""
    with pytest.raises(AuthError):
        ApiKeyAuthenticator.from_config(raw)


def test_unknown_credential_is_rejected():
    auth = ApiKeyAuthenticator.from_config(ALICE_KEY)
    assert auth.authenticate("sk-wrong") is None
    assert auth.authenticate("") is None
    assert auth.authenticate(None) is None


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ([(b"authorization", b"Bearer sk-1")], "sk-1"),
        ([(b"Authorization", b"bearer sk-1")], "sk-1"),
        ([(b"x-api-key", b"sk-1")], "sk-1"),
        ([(b"authorization", b"Basic sk-1")], None),
        ([(b"authorization", b"Bearer")], None),
        ([], None),
    ],
)
def test_bearer_token_extraction(headers, expected):
    assert bearer_token(headers) == expected


# -- the gate -----------------------------------------------------------------
async def test_unauthenticated_requests_are_rejected():
    async with _client(ApiKeyAuthenticator.from_config(ALICE_KEY)) as client:
        response = await client.post("/mcp", json={})

    assert response.status_code == 401
    # RFC 6750: tell the client how to authenticate rather than leaving it to guess.
    assert response.headers["www-authenticate"] == 'Bearer realm="coletar"'
    assert response.json()["error"] == "unauthorized"


async def test_a_valid_key_reaches_the_application_as_a_named_principal():
    async with _client(ApiKeyAuthenticator.from_config(ALICE_KEY)) as client:
        response = await client.post("/mcp", headers={"Authorization": "Bearer sk-alice"})

    assert response.status_code == 200
    assert response.text == "alice"


async def test_a_server_with_no_keys_rejects_everything():
    async with _client(ApiKeyAuthenticator.from_config("")) as client:
        assert (await client.post("/mcp")).status_code == 401
        assert (await client.post("/mcp", headers={"Authorization": "Bearer x"})).status_code == 401


async def test_health_check_is_the_only_exemption():
    """A liveness probe cannot carry a credential. Nothing else may claim that."""
    assert set(EXEMPT_PATHS) == {"/healthz"}

    async with _client(ApiKeyAuthenticator.from_config(ALICE_KEY)) as client:
        assert (await client.get("/healthz")).status_code == 200
        for path in ("/mcp", "/", "/healthz/../mcp", "/metrics"):
            assert (await client.get(path)).status_code == 401, path


async def test_the_principal_does_not_leak_between_requests():
    async with _client(ApiKeyAuthenticator.from_config(ALICE_KEY)) as client:
        await client.post("/mcp", headers={"Authorization": "Bearer sk-alice"})
    assert current_principal() is None


def test_principal_scope_restores_the_previous_binding():
    assert current_principal() is None
    with principal_scope(Principal(tenant_id=TENANT, id="outer")):
        assert current_principal() is not None
        with principal_scope(Principal(tenant_id=TENANT, id="inner")):
            assert current_principal().id == "inner"
        assert current_principal().id == "outer"
    assert current_principal() is None


async def test_auth_validation_stays_under_fifty_milliseconds(benchmark_keys=None):
    """M3.1's budget. Constant-time comparison runs against every configured secret,
    so the cost grows with the key count — measured at a realistic fleet size."""
    import time

    entries = [
        {"id": f"user-{i}", "secret": f"sk-{i:04d}", "tenant_id": f"tenant_{i:04d}"}
        for i in range(500)
    ]
    auth = ApiKeyAuthenticator.from_config(json.dumps(entries))

    durations: list[float] = []
    for i in (0, 250, 499):
        for _ in range(50):
            started = time.perf_counter()
            assert auth.authenticate(f"sk-{i:04d}") is not None
            durations.append((time.perf_counter() - started) * 1000)
    # And the miss path, which compares against every key before giving up.
    for _ in range(50):
        started = time.perf_counter()
        assert auth.authenticate("sk-nope") is None
        durations.append((time.perf_counter() - started) * 1000)

    durations.sort()
    p95 = durations[int(0.95 * len(durations)) - 1]
    assert p95 < 50.0, f"auth p95 {p95:.2f}ms across {len(entries)} keys"
