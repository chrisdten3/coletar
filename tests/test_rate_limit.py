"""M7 — per-principal rate limiting.

The roadmap gates the public API on this in the same breath as auth and tenant
isolation, and the three belong together because they answer one question: what can a
single credential do to everyone else's service. Tenant isolation stops a key reading
another graph; this stops a key exhausting the machine both graphs live on.
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from coletar.mcp.auth import ApiKeyAuthenticator, AuthMiddleware
from coletar.mcp.ratelimit import RateLimiter

KEYS = json.dumps(
    [
        {"id": "alice", "secret": "sk-alice", "tenant_id": "tenant_alice"},
        {"id": "bob", "secret": "sk-bob", "tenant_id": "tenant_bob"},
    ]
)


def app_with(limiter: RateLimiter) -> TestClient:
    inner = FastAPI()

    @inner.get("/v1/ping")
    async def ping() -> JSONResponse:
        return JSONResponse({"ok": True})

    @inner.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    wrapped = AuthMiddleware(
        inner, ApiKeyAuthenticator.from_config(KEYS), rate_limiter=limiter
    )
    return TestClient(wrapped)


def get(client: TestClient, secret: str = "sk-alice"):
    return client.get("/v1/ping", headers={"Authorization": f"Bearer {secret}"})


# --- the bucket itself ------------------------------------------------------------


def test_a_burst_is_allowed_then_refused() -> None:
    """A connector opening a conversation legitimately fires several calls at once.
    Refusing that would break normal use to prevent nothing."""
    limiter = RateLimiter(requests_per_minute=60, burst=5)
    assert [limiter.check("alice", now=0.0) for _ in range(5)] == [None] * 5
    assert limiter.check("alice", now=0.0) is not None


def test_it_reports_how_long_to_wait_not_just_no() -> None:
    """A client told only "no" retries immediately and makes the problem worse."""
    limiter = RateLimiter(requests_per_minute=60, burst=1)
    assert limiter.check("alice", now=0.0) is None
    wait = limiter.check("alice", now=0.0)
    assert wait is not None and 0.0 < wait <= 1.5


def test_it_refills_continuously_rather_than_on_a_window_edge() -> None:
    """A fixed window lets a caller save up a minute of quota and spend it at once."""
    limiter = RateLimiter(requests_per_minute=60, burst=2)
    limiter.check("alice", now=0.0)
    limiter.check("alice", now=0.0)
    assert limiter.check("alice", now=0.0) is not None
    assert limiter.check("alice", now=1.01) is None      # one token back after ~1s
    assert limiter.check("alice", now=1.01) is not None


def test_a_bucket_cannot_exceed_its_burst_however_long_it_idles() -> None:
    limiter = RateLimiter(requests_per_minute=60, burst=3)
    limiter.check("alice", now=0.0)
    allowed = sum(1 for _ in range(10) if limiter.check("alice", now=10_000.0) is None)
    assert allowed == 3


def test_the_limit_is_keyed_by_credential_not_by_address() -> None:
    """Several users behind one office NAT are not one caller, and one caller
    rotating addresses is not several. The credential is what the server knows."""
    limiter = RateLimiter(requests_per_minute=60, burst=2)
    assert limiter.check("alice", now=0.0) is None
    assert limiter.check("alice", now=0.0) is None
    assert limiter.check("alice", now=0.0) is not None
    # Bob is untouched by Alice exhausting hers.
    assert limiter.check("bob", now=0.0) is None


# --- through the middleware -------------------------------------------------------


def test_a_limited_caller_gets_429_with_a_truthful_retry_after() -> None:
    client = app_with(RateLimiter(requests_per_minute=60, burst=2))
    assert get(client).status_code == 200
    assert get(client).status_code == 200

    response = get(client)
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1
    assert "rate limited" in response.json()["error"]


def test_one_key_running_away_does_not_stop_another() -> None:
    client = app_with(RateLimiter(requests_per_minute=60, burst=2))
    for _ in range(5):
        get(client, "sk-alice")
    assert get(client, "sk-alice").status_code == 429
    assert get(client, "sk-bob").status_code == 200


def test_an_unauthenticated_request_is_refused_before_it_costs_quota() -> None:
    """401 first, so an attacker without a key cannot exhaust someone else's bucket
    — and cannot use timing to learn whose key is busy."""
    limiter = RateLimiter(requests_per_minute=60, burst=1)
    client = app_with(limiter)
    for _ in range(10):
        assert client.get("/v1/ping", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert get(client).status_code == 200


def test_healthz_is_never_rate_limited() -> None:
    """A liveness probe that can be throttled will eventually take the service down
    by reporting it unhealthy under exactly the load it should survive."""
    client = app_with(RateLimiter(requests_per_minute=60, burst=1))
    for _ in range(20):
        assert client.get("/healthz").status_code == 200


def test_the_mcp_surface_and_the_rest_surface_share_one_bucket(monkeypatch) -> None:
    """A limit one surface can walk around is not a limit, so it is applied in the
    middleware both are mounted behind rather than per route."""
    from coletar.config import get_settings
    from coletar.mcp import server as mcp_server

    monkeypatch.setenv("COLETAR_MCP_API_KEYS", KEYS)
    monkeypatch.setenv("COLETAR_RATE_LIMIT_PER_MINUTE", "42")
    get_settings.cache_clear()
    try:
        app = mcp_server.build_app()
        assert isinstance(app, AuthMiddleware)
        assert app.rate_limiter.requests_per_minute == 42
    finally:
        get_settings.cache_clear()
