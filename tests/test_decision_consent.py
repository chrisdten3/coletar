"""M11.1: consent for raw decision capture, and the capture path it gates.

Three properties are asserted here because each is a promise rather than a
behaviour: raw capture is refused for a tool nobody opted in, granting it takes a
scope an ordinary integration key does not hold, and revoking it destroys what was
already captured rather than merely declining the next call.
"""

from __future__ import annotations

import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from coletar.decisions import consent
from coletar.decisions.raw_capture import DECISION_RAW, EPISODE_KIND, RAW_TTL_DAYS
from coletar.episode_crypto import EpisodeKeyUnavailable, decrypt_episode
from coletar.mcp import rest
from coletar.mcp.auth import ApiKeyAuthenticator, AuthMiddleware
from coletar.schema.events import EventType
from coletar.schema.objects import ObjectType
from coletar.store.memory import InMemoryStore
from conftest import TENANT

WRITE_KEY = "sk-integration"
CONSENT_KEY = "sk-operator"
WRITE_AUTH = {"X-API-Key": WRITE_KEY}
CONSENT_AUTH = {"X-API-Key": CONSENT_KEY}
TOOL = "route_ticket"

KEYS = json.dumps(
    [
        {"id": "integration", "secret": WRITE_KEY, "tenant_id": str(TENANT)},
        {
            "id": "operator",
            "secret": CONSENT_KEY,
            "tenant_id": str(TENANT),
            "scopes": ["read", "write", "consent"],
        },
    ]
)


@pytest.fixture
def store(monkeypatch) -> InMemoryStore:
    backing = InMemoryStore()
    monkeypatch.setattr(rest, "build_store", lambda: backing)
    return backing


@pytest.fixture
def client():
    inner = Starlette(routes=[Route(p, e, methods=m) for p, e, m in rest.routes()])
    app = AuthMiddleware(
        inner, ApiKeyAuthenticator.from_config(KEYS), allowed_origins=frozenset()
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _grant(client, *, tool=TOOL, consent_type="raw_capture", confirm=True, auth=None):
    return await client.post(
        "/v1/decisions/consent",
        json={"tool_name": tool, "consent_type": consent_type, "confirm": confirm},
        headers=auth or CONSENT_AUTH,
    )


async def _capture(client, *, tool=TOOL):
    return await client.post(
        "/v1/decisions/raw",
        json={
            "tool_name": tool,
            "intent": "customer is asking about a duplicate charge",
            "environmental_state": {"tier": "enterprise", "prior_disputes": 3},
            "tool_call": {"name": tool, "arguments": {"queue": "billing"}},
            "tool_response": {"team": "priority-billing"},
        },
        headers=WRITE_AUTH,
    )


# -- the scope boundary -------------------------------------------------------
async def test_an_ordinary_write_key_cannot_grant_consent(client, store):
    """The key that reports decisions must not be able to widen what is captured."""
    async with client as c:
        response = await _grant(c, auth=WRITE_AUTH)
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


async def test_consent_requires_explicit_confirmation(client, store):
    async with client as c:
        response = await _grant(c, confirm=False)
    assert response.status_code == 400
    assert response.json()["error"] == "confirmation_required"
    assert not await consent.is_consented(store, TENANT, TOOL, consent.RAW_CAPTURE)


# -- the capture gate ---------------------------------------------------------
async def test_raw_capture_is_refused_without_consent(client, store):
    async with client as c:
        response = await _capture(c)
    assert response.status_code == 403
    assert response.json()["error"] == "raw_capture_not_consented"
    assert await store.list_objects(TENANT, type=ObjectType.EPISODE) == []


async def test_consent_is_per_tool_not_per_tenant(client, store):
    """Opting one tool in says nothing about another."""
    async with client as c:
        await _grant(c, tool=TOOL)
        allowed = await _capture(c, tool=TOOL)
        refused = await _capture(c, tool="approve_refund")
    assert allowed.status_code == 200
    assert refused.status_code == 403


async def test_a_consented_trace_is_stored_encrypted_and_short_lived(client, store):
    async with client as c:
        await _grant(c)
        response = await _capture(c)
    assert response.status_code == 200 and response.json()["captured"] is True

    episodes = await store.list_objects(TENANT, type=ObjectType.EPISODE)
    assert len(episodes) == 1
    trace = episodes[0]
    assert trace.payload[EPISODE_KIND] == DECISION_RAW
    assert trace.payload["tool_name"] == TOOL
    assert trace.ttl_days == RAW_TTL_DAYS
    # Ciphertext at rest, never the plaintext the caller posted.
    assert "duplicate charge" not in trace.content
    recovered = json.loads(await decrypt_episode(store, TENANT, trace))
    assert recovered["tool_response"] == {"team": "priority-billing"}


async def test_a_grant_is_recorded_in_the_event_log(client, store):
    """Consent is the fact this feature's liability rests on, so it is not just config."""
    async with client as c:
        await _grant(c)
    granted = [
        e for e in await store.list_events(TENANT) if e.type is EventType.CONSENT_GRANTED
    ]
    assert len(granted) == 1
    assert granted[0].detail["tool_name"] == TOOL
    assert granted[0].detail["principal"] == "operator"


# -- revocation purges --------------------------------------------------------
async def test_revoking_consent_shreds_what_was_already_captured(client, store):
    async with client as c:
        await _grant(c)
        await _capture(c)
        revoked = await c.post(
            "/v1/decisions/consent/revoke",
            json={"tool_name": TOOL, "consent_type": "raw_capture"},
            headers=CONSENT_AUTH,
        )
    assert revoked.status_code == 200
    assert revoked.json() == {"revoked": True, "purged_raw_traces": 1}

    # The object survives for provenance; its content is unrecoverable.
    trace = (await store.list_objects(TENANT, type=ObjectType.EPISODE))[0]
    with pytest.raises(EpisodeKeyUnavailable):
        await decrypt_episode(store, TENANT, trace)


async def test_capture_stops_after_revocation(client, store):
    async with client as c:
        await _grant(c)
        await c.post(
            "/v1/decisions/consent/revoke",
            json={"tool_name": TOOL, "consent_type": "raw_capture"},
            headers=CONSENT_AUTH,
        )
        response = await _capture(c)
    assert response.status_code == 403


async def test_an_oversized_field_is_rejected(client, store):
    async with client as c:
        await _grant(c)
        response = await c.post(
            "/v1/decisions/raw",
            json={"tool_name": TOOL, "intent": "x" * (rest.MAX_RAW_FIELD_CHARS + 1)},
            headers=WRITE_AUTH,
        )
    assert response.status_code == 400
