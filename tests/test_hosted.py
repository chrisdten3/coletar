"""The hosted workspace requires a signed-in account; the other authorities are separate.

Rewritten 2026-09-18, when the deployment stopped being single-owner. The previous
version of this file pinned the *opposite* property — `test_workspace_serves_anonymously`
asserted that `/web-api/state` returned 200 with no credential — because the
deployment was deliberately open. Both cannot be true, and the old assertion is not
something to keep passing by loosening it: it is deleted and replaced by its inverse.

What these tests hold:

  * anonymous requests reach nothing, and a *connector* key is not a workspace session
  * two signed-in accounts cannot see each other's graphs
  * the scheduler still needs `CRON_SECRET`, and now runs across every account
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coletar.accounts.models import ExternalIdentity
from coletar.config import get_settings
from coletar.hosted import create_app
from coletar.store import reset_store
from coletar.store.memory import InMemoryStore

#: Session tokens the stub provider below recognises. Real Clerk JWT verification is
#: exercised in `test_clerk_identity.py`; nothing here should re-test a signature.
SESSIONS = {
    "session-alice": ExternalIdentity(
        provider="clerk",
        subject="user_alice",
        email="alice@example.com",
        display_name="Alice",
        email_verified=True,
    ),
    "session-bob": ExternalIdentity(
        provider="clerk",
        subject="user_bob",
        email="bob@example.com",
        display_name="Bob",
        email_verified=True,
    ),
    "session-mallory": ExternalIdentity(
        provider="clerk",
        subject="user_mallory",
        email="mallory@example.com",
        email_verified=True,
    ),
}


class StubClerk:
    """Stands in for Clerk's JWKS check, and only for that."""

    name = "clerk"

    async def verify(self, credential):
        from coletar.accounts.identity import IdentityError

        if not credential:
            return None
        if credential not in SESSIONS:
            raise IdentityError("This sign-in could not be verified.")
        return SESSIONS[credential]


def as_(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer session-{who}"}


def await_(coro):
    """Run one coroutine from a sync test. These tests drive a TestClient, which is
    itself sync, so there is no running loop to await on."""
    import asyncio

    return asyncio.run(coro)


@pytest.fixture
def hosted(monkeypatch, tmp_path):
    import coletar.accounts
    import coletar.store
    from coletar.accounts.memory import InMemoryDirectory

    monkeypatch.setenv("COLETAR_STORE_BACKEND", "postgres")
    monkeypatch.setenv("COLETAR_PUBLIC_URL", "https://coletar.example")
    monkeypatch.setenv("COLETAR_IDENTITY_PROVIDER", "clerk")
    # Alice and Bob may provision; Mallory authenticates perfectly well and is not
    # on the list, which is the case the invite gate exists for.
    monkeypatch.setenv("COLETAR_INVITE_ALLOWLIST", "alice@example.com, bob@example.com")
    monkeypatch.setenv("COLETAR_MCP_ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv(
        "COLETAR_MCP_API_KEYS",
        json.dumps(
            [
                {
                    "id": "claude",
                    "secret": "connector-key",
                    "tenant_id": "tenant_test",
                    "surface": "claude",
                    "scopes": ["read", "write"],
                },
            ]
        ),
    )
    get_settings.cache_clear()
    monkeypatch.setattr(coletar.store, "_singleton", InMemoryStore())
    monkeypatch.setattr(
        coletar.accounts, "_singleton", InMemoryDirectory(Path(tmp_path) / "accounts.json")
    )
    monkeypatch.setattr(coletar.accounts, "_identity", StubClerk())
    with TestClient(create_app()) as client:
        yield client
    get_settings.cache_clear()
    reset_store()
    coletar.accounts.reset_directory()


def test_workspace_refuses_anonymous_requests(hosted):
    """The inverse of what this file used to assert. See the module docstring."""
    for path in ("/web-api/state", "/web-api/connections", "/web-api/graph"):
        assert hosted.get(path).status_code == 401
    # A *connector* key is a different authority and is not a workspace session.
    # This is the confusion that would quietly reopen the workspace: both arrive as
    # `Authorization: Bearer`, and only one of them is a sign-in.
    connector = hosted.get("/web-api/state", headers={"Authorization": "Bearer connector-key"})
    assert connector.status_code == 401
    # A forged or expired session is refused the same way as none at all.
    forged = hosted.get("/web-api/state", headers={"Authorization": "Bearer forged"})
    assert forged.status_code == 401
    # The shell itself still loads: it has to, or there is nowhere to sign in.
    assert hosted.get("/app").status_code == 200
    assert hosted.get("/").status_code == 200


def test_signed_in_account_gets_its_own_graph(hosted):
    state = hosted.get("/web-api/state", headers=as_("alice"))
    assert state.status_code == 200
    body = state.json()
    assert body["hosted"] is True
    assert body["objects"] == []
    # Provisioned on first sign-in, with a tenant derived from the address rather
    # than the configured default.
    assert body["tenant"] != "tenant_local"
    assert state.headers["cache-control"] == "no-store"
    posture = hosted.get("/web-api/connections", headers=as_("alice")).json()
    assert posture["public_workspace"] is False
    assert posture["account_auth"] is True


def test_two_accounts_cannot_reach_each_other(hosted):
    written = hosted.post(
        "/web-api/memories",
        json={"content": "Alice prefers integer cents for money."},
        headers=as_("alice"),
    )
    assert written.status_code == 200
    object_id = written.json()["id"]

    mine = hosted.get("/web-api/state", headers=as_("alice")).json()["objects"]
    assert any("integer cents" in o["content"] for o in mine)

    theirs = hosted.get("/web-api/state", headers=as_("bob")).json()["objects"]
    assert theirs == []
    # Not merely absent from the list — unreachable by direct id, which is the
    # check that matters if an id ever leaks.
    assert hosted.get(f"/web-api/objects/{object_id}/reads", headers=as_("bob")).status_code == 404
    assert (
        hosted.post(
            f"/web-api/objects/{object_id}",
            json={"action": "retire"},
            headers=as_("bob"),
        ).status_code
        == 404
    )


def test_uninvited_but_authentic_sign_in_is_refused_distinctly(hosted):
    """403, not 401: "not on the list yet" and "your sign-in is broken" differ."""
    response = hosted.get("/web-api/state", headers=as_("mallory"))
    assert response.status_code == 403
    assert "invite" in response.json()["detail"].lower()
    # And no workspace was quietly created for them on the way to refusing.
    import coletar.accounts

    directory = coletar.accounts.build_directory()
    assert await_(directory.account_by_email("mallory@example.com")) is None


def test_workspace_session_does_not_open_the_connector_api(hosted):
    """Signing in to the page is not a scope on `/mcp` or `/v1`."""
    search = hosted.post("/v1/search", json={"query": "test"}, headers=as_("alice"))
    assert search.status_code == 401
    ping = hosted.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=as_("alice")
    )
    assert ping.status_code == 401
    # `same_origin` survives as defence in depth alongside the bearer token.
    denied = hosted.post(
        "/web-api/memories",
        json={"content": "Injected"},
        headers={**as_("alice"), "Origin": "https://evil.example"},
    )
    assert denied.status_code == 403


def test_healthz_reports_the_database(hosted):
    """The probe touches the store, so it can fail when the store is unreachable."""
    body = hosted.get("/healthz").json()
    assert body == {"status": "ok", "database": "connected"}


def test_healthz_degrades_when_the_store_is_unreachable(hosted, monkeypatch):
    import coletar.store

    class Broken:
        async def list_objects(self, *args, **kwargs):
            raise ConnectionError("no route to host")

    monkeypatch.setattr(coletar.store, "_singleton", Broken())
    response = hosted.get("/healthz")
    assert response.status_code == 503
    assert response.json()["database"] == "unreachable"
    # No connection detail leaks through an endpoint that cannot carry a credential.
    assert "no route to host" not in response.text


def test_hosted_stateless_protocol_and_rest_share_graph(hosted):
    headers = {
        "Authorization": "Bearer connector-key",
        "Accept": "application/json, text/event-stream",
    }
    initialized = hosted.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "hosted-test", "version": "1"},
            },
        },
    )
    assert initialized.status_code == 200
    assert "mcp-session-id" not in initialized.headers
    assert "serverInfo" in initialized.json()["result"]
    written = hosted.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "write_memory",
                "arguments": {"content": "Use integer cents for money."},
            },
        },
    )
    assert written.status_code == 200
    assert not written.json()["result"].get("isError")
    response = hosted.post("/v1/search", headers=headers, json={"query": "integer cents"})
    assert response.status_code == 200
    assert "integer cents" in response.text


def test_hosted_refuses_ephemeral_storage(monkeypatch):
    monkeypatch.setenv("COLETAR_STORE_BACKEND", "memory")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="requires Postgres"), TestClient(create_app()):
            pass
    finally:
        get_settings.cache_clear()


def test_hosted_refuses_local_identity_provider(monkeypatch):
    """A hosted deployment cannot end up on laptop auth by nobody changing a setting."""
    from coletar.accounts import IdentityError, build_identity, reset_directory

    monkeypatch.setenv("COLETAR_PUBLIC_URL", "https://coletar.example")
    monkeypatch.setenv("COLETAR_IDENTITY_PROVIDER", "local")
    get_settings.cache_clear()
    reset_directory()
    try:
        with pytest.raises(IdentityError, match="laptop assumption"):
            build_identity()
    finally:
        get_settings.cache_clear()
        reset_directory()


def test_scheduled_worker_requires_its_own_key_and_covers_every_account(hosted, monkeypatch):
    """The scheduler's pass is per-account now, not per configured tenant."""
    from coletar.jobs.worker import WorkerPass

    called = []

    async def fake_pass(store, tenant):
        called.append(str(tenant))
        return WorkerPass(tenant_id=str(tenant), owner="test", extraction={"processed": 1})

    monkeypatch.setattr("coletar.hosted.run_pass", fake_pass)
    monkeypatch.setenv("CRON_SECRET", "scheduler-key")
    monkeypatch.setenv("COLETAR_CAPTURE_TURNS", "false")
    get_settings.cache_clear()

    assert hosted.get("/api/jobs/capture").status_code == 401
    headers = {"Authorization": "Bearer scheduler-key"}
    assert hosted.get("/api/jobs/capture", headers=headers).status_code == 409
    assert not called

    # Two accounts exist once they have signed in once.
    hosted.get("/web-api/state", headers=as_("alice"))
    hosted.get("/web-api/state", headers=as_("bob"))

    monkeypatch.setenv("COLETAR_CAPTURE_TURNS", "true")
    monkeypatch.setenv("COLETAR_EXTRACTION_PROVIDER", "openai")
    get_settings.cache_clear()
    body = hosted.get("/api/jobs/capture", headers=headers).json()
    assert body["tenants"] == 2
    assert body["extraction"]["processed"] == 2
    assert len(called) == 2
    assert len(set(called)) == 2


def test_on_demand_batch_is_scoped_to_the_caller(hosted, monkeypatch):
    """One account's button must not spend the whole deployment's extraction budget."""
    from coletar.jobs.worker import WorkerPass

    called = []

    async def fake_pass(store, tenant):
        called.append(str(tenant))
        return WorkerPass(tenant_id=str(tenant), owner="test", extraction={"processed": 1})

    monkeypatch.setattr("coletar.hosted.run_pass", fake_pass)
    monkeypatch.setenv("COLETAR_CAPTURE_TURNS", "true")
    monkeypatch.setenv("COLETAR_EXTRACTION_PROVIDER", "openai")
    get_settings.cache_clear()

    alice_tenant = hosted.get("/web-api/state", headers=as_("alice")).json()["tenant"]
    hosted.get("/web-api/state", headers=as_("bob"))

    body = hosted.post("/web-api/process-captures", headers=as_("alice")).json()
    assert body["tenants"] == 1
    assert called == [alice_tenant]


# -- the shell's sign-in config -------------------------------------------------
def test_shell_tells_the_client_sign_in_is_required(hosted):
    """The client cannot decide whether to render before it knows this.

    Stamped into the HTML rather than fetched: a round trip here is one during
    which the app does not know whether it may draw, which is how a stranger sees
    someone else's workspace for a frame before being told to sign in.
    """
    import json
    import re

    html = hosted.get("/app").text
    match = re.search(
        r'<script id="sign-in-config" type="application/json">(.*?)</script>', html, re.S
    )
    assert match, "the shell did not stamp its sign-in config"
    config = json.loads(match.group(1))
    assert config["required"] is True
    assert config["provider"] == "clerk"
    # Publishable by definition. The assertion that matters is the absence of any
    # *secret*: coleta reads no Clerk secret key, because verification is a
    # signature check against a public JWKS.
    assert "secret" not in html.lower().replace("secrets", "")


def test_local_shell_asks_for_no_sign_in(monkeypatch, tmp_path):
    """Local development has no login and must keep working with none."""
    import json
    import re

    from fastapi.testclient import TestClient as _TestClient

    import coletar.accounts
    import coletar.store
    from coletar.inspector.app import app as local_app

    monkeypatch.delenv("COLETAR_PUBLIC_URL", raising=False)
    monkeypatch.setenv("COLETAR_IDENTITY_PROVIDER", "local")
    monkeypatch.setenv("COLETAR_STORE_BACKEND", "memory")
    monkeypatch.setenv("COLETAR_STORE_PATH", str(tmp_path / "graph.json"))
    get_settings.cache_clear()
    monkeypatch.setattr(coletar.store, "_singleton", InMemoryStore())
    coletar.accounts.reset_directory()
    try:
        with _TestClient(local_app) as client:
            html = client.get("/app").text
            # And the workspace itself still answers without any credential.
            assert client.get("/web-api/state").status_code == 200
    finally:
        get_settings.cache_clear()
        coletar.accounts.reset_directory()

    config = json.loads(
        re.search(
            r'<script id="sign-in-config" type="application/json">(.*?)</script>', html, re.S
        ).group(1)
    )
    assert config["required"] is False
    assert config["provider"] == "local"
