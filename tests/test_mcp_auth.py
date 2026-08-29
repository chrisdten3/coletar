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
    EXEMPT_PREFIXES,
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


async def test_only_liveness_and_discovery_are_exempt():
    """Two exemptions, each with a reason a credential cannot exist yet: a liveness
    probe has none to send, and a client cannot authenticate before discovering how
    to authenticate. Nothing else may claim that."""
    assert set(EXEMPT_PATHS) == {"/healthz"}
    assert EXEMPT_PREFIXES == ("/.well-known/",)

    async with _client(ApiKeyAuthenticator.from_config(ALICE_KEY)) as client:
        assert (await client.get("/healthz")).status_code == 200
        for path in ("/mcp", "/", "/healthz/../mcp", "/metrics", "/v1/search"):
            assert (await client.get(path)).status_code == 401, path


async def test_discovery_reports_absence_rather_than_rejection():
    """We implement no OAuth, so these should 404 — "this server does not do OAuth" —
    rather than 401, which claims the caller's credentials are wrong for discovery
    and turns a plain auth failure into an unexplained connection error."""
    from starlette.applications import Starlette

    app = AuthMiddleware(Starlette(), ApiKeyAuthenticator.from_config(ALICE_KEY))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-authorization-server",
            "/.well-known/oauth-protected-resource/mcp",
        ):
            assert (await client.get(path)).status_code == 404, path


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


# -- M3.3: the deployment guard ------------------------------------------------
@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]"])
def test_a_public_bind_on_the_in_process_store_is_refused(host, monkeypatch):
    """A reachable server on the in-process store loses its whole graph on restart,
    silently, while every request succeeds. That is a configuration mistake rather
    than a choice, so it fails at boot instead of at whatever hour the container is
    first rescheduled."""
    from coletar.config import get_settings
    from coletar.mcp.server import check_deployable

    get_settings.cache_clear()
    monkeypatch.setenv("COLETAR_STORE_BACKEND", "memory")
    try:
        with pytest.raises(AuthError, match="refusing to bind"):
            check_deployable(host)
    finally:
        get_settings.cache_clear()


def test_a_public_bind_on_postgres_is_allowed(monkeypatch):
    from coletar.config import get_settings
    from coletar.mcp.server import check_deployable

    get_settings.cache_clear()
    monkeypatch.setenv("COLETAR_STORE_BACKEND", "postgres")
    try:
        check_deployable("0.0.0.0")
    finally:
        get_settings.cache_clear()


def test_loopback_on_the_in_process_store_stays_fine(monkeypatch):
    """The zero-infrastructure path must keep working — it is only *public* exposure
    that the guard is about."""
    from coletar.config import get_settings
    from coletar.mcp.server import check_deployable

    get_settings.cache_clear()
    monkeypatch.setenv("COLETAR_STORE_BACKEND", "memory")
    try:
        check_deployable("127.0.0.1")
    finally:
        get_settings.cache_clear()


# -- M3.3: DNS-rebinding protection --------------------------------------------
def test_localhost_only_by_default(monkeypatch):
    """Unconfigured means the SDK's own default: localhost only. Right for local
    development, and the reason a deployment must declare itself."""
    from coletar.config import get_settings
    from coletar.mcp.server import transport_security

    get_settings.cache_clear()
    monkeypatch.delenv("COLETAR_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("COLETAR_MCP_ALLOWED_HOSTS", "")
    try:
        assert transport_security() is None
    finally:
        get_settings.cache_clear()


def test_configured_hosts_are_declared_to_the_transport(monkeypatch):
    """The bug this pins: a deployment on a real domain refused every request with
    421 Misdirected Request *after* authentication succeeded, because the SDK trusts
    only localhost unless told otherwise. It looked like anything but a host check."""
    from coletar.config import get_settings
    from coletar.mcp.server import transport_security

    get_settings.cache_clear()
    monkeypatch.setenv("COLETAR_MCP_ALLOWED_HOSTS", "coletar-mcp.fly.dev, example.test")
    try:
        settings = transport_security()
        assert settings is not None
        assert settings.allowed_hosts == ["coletar-mcp.fly.dev", "example.test"]
        assert settings.allowed_origins == [
            "https://coletar-mcp.fly.dev",
            "https://example.test",
        ]
        assert settings.enable_dns_rebinding_protection is True
    finally:
        get_settings.cache_clear()
