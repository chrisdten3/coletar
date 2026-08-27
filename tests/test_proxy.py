"""The proxy is the wedge, so its two jobs get real tests: inject on the way in,
extract on the way out — without ever reaching a live model server."""

import httpx
import pytest
from fastapi.testclient import TestClient

from coletar.proxy import app as proxy_module
from coletar.schema import Memory, MemoryKind
from coletar.store import InMemoryStore


@pytest.fixture
def store(monkeypatch):
    s = InMemoryStore()
    monkeypatch.setattr(proxy_module, "build_store", lambda: s)
    return s


@pytest.fixture
def upstream(monkeypatch):
    """Capture what the proxy forwards, and reply with a canned completion."""
    seen: dict = {}

    async def fake_post(self, url, *, json=None, headers=None):
        seen["url"] = url
        seen["body"] = json
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "Understood."}}]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return seen


async def test_retrieved_memory_is_injected_as_a_system_message(store, upstream):
    await store.put_object(
        Memory.from_write("Chris prefers fixed-point integers for money.",
                          kind=MemoryKind.PREFERENCE)
    )

    with TestClient(proxy_module.app) as client:
        client.post(
            "/v1/chat/completions",
            json={"model": "llama3", "messages": [
                {"role": "user", "content": "How should I represent money?"}
            ]},
        )

    system = upstream["body"]["messages"][0]
    assert system["role"] == "system"
    assert "fixed-point" in system["content"]
    # The model must be told this is background, not a user instruction.
    assert "not as instructions" in system["content"]


async def test_injection_merges_into_an_existing_system_message(store, upstream):
    """Several local runtimes only honour the first system message."""
    await store.put_object(Memory.from_write("Chris prefers concise answers."))

    with TestClient(proxy_module.app) as client:
        client.post(
            "/v1/chat/completions",
            json={"model": "llama3", "messages": [
                {"role": "system", "content": "You are terse."},
                {"role": "user", "content": "Tell me about chris and answers."},
            ]},
        )

    messages = upstream["body"]["messages"]
    assert sum(1 for m in messages if m["role"] == "system") == 1
    assert "You are terse." in messages[0]["content"]
    assert "concise" in messages[0]["content"]


async def test_new_memory_is_extracted_from_the_users_turn(store, upstream):
    with TestClient(proxy_module.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "llama3", "messages": [
                {"role": "user", "content": "From now on, always use uv instead of pip."}
            ]},
        )

    assert response.status_code == 200
    stored = await store.list_objects()
    assert any("uv instead of pip" in o.content for o in stored)


async def test_nothing_is_injected_when_the_graph_is_empty(store, upstream):
    with TestClient(proxy_module.app) as client:
        client.post(
            "/v1/chat/completions",
            json={"model": "llama3", "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert upstream["body"]["messages"][0]["role"] == "user"
