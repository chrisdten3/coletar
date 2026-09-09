"""Hosted owner authority must remain separate from connector authority."""

import json

import pytest
from fastapi.testclient import TestClient

from coletar.config import get_settings
from coletar.hosted import create_app
from coletar.store import reset_store
from coletar.store.memory import InMemoryStore

PASSWORD = "test-owner-password-longer-than-24"


@pytest.fixture
def hosted(monkeypatch):
    import coletar.store

    monkeypatch.setenv("COLETAR_STORE_BACKEND", "postgres")
    monkeypatch.setenv("COLETAR_WEB_USERNAME", "coletar")
    monkeypatch.setenv("COLETAR_WEB_PASSWORD", PASSWORD)
    monkeypatch.setenv("COLETAR_PUBLIC_URL", "https://coletar.example")
    monkeypatch.setenv("COLETAR_DEFAULT_TENANT_ID", "tenant_test")
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
    with TestClient(create_app()) as client:
        yield client
    get_settings.cache_clear()
    reset_store()


def test_owner_endpoints_reject_connector_keys_and_anonymous_requests(hosted):
    for path in ("/", "/app", "/web-api/state", "/web-api/connections"):
        assert hosted.get(path).status_code == 401
        assert (
            hosted.get(path, headers={"Authorization": "Bearer connector-key"}).status_code == 401
        )
    assert hosted.get("/healthz").json() == {"status": "ok"}
    response = hosted.get("/web-api/state", auth=("coletar", PASSWORD))
    assert response.status_code == 200
    assert response.json()["hosted"] is True
    assert response.headers["cache-control"] == "no-store"
    assert hosted.get("/edit", auth=("coletar", PASSWORD)).status_code != 200


def test_owner_password_does_not_unlock_connector_and_writes_require_same_origin(hosted):
    assert (
        hosted.post("/v1/search", json={"query": "test"}, auth=("coletar", PASSWORD)).status_code
        == 401
    )
    denied = hosted.post(
        "/web-api/memories",
        json={"content": "Injected"},
        auth=("coletar", PASSWORD),
        headers={"Origin": "https://evil.example"},
    )
    assert denied.status_code == 403
    assert hosted.get("/web-api/state", auth=("coletar", PASSWORD)).json()["objects"] == []


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
    objects = hosted.get("/web-api/state", auth=("coletar", PASSWORD)).json()["objects"]
    assert any("integer cents" in o["content"] for o in objects)


def test_hosted_refuses_ephemeral_storage(monkeypatch):
    monkeypatch.setenv("COLETAR_STORE_BACKEND", "memory")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="requires Postgres"), TestClient(create_app()):
            pass
    finally:
        get_settings.cache_clear()


def test_scheduled_worker_requires_its_own_key_and_explicit_optin(hosted, monkeypatch):
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
    assert hosted.get("/api/jobs/capture", auth=("coletar", PASSWORD)).status_code == 401
    headers = {"Authorization": "Bearer scheduler-key"}
    assert hosted.get("/api/jobs/capture", headers=headers).status_code == 409
    assert not called
    monkeypatch.setenv("COLETAR_CAPTURE_TURNS", "true")
    monkeypatch.setenv("COLETAR_EXTRACTION_PROVIDER", "openai")
    get_settings.cache_clear()
    assert hosted.get("/api/jobs/capture", headers=headers).json()["extraction"]["processed"] == 1
    assert called == ["tenant_test"]
    assert hosted.get("/web-api/state", headers=headers).status_code == 401
