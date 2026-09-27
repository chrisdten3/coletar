"""M11.3: the promotion gate in front of automated resolution.

The load-bearing claim of this tier is that nothing decides automatically unless a
person approved that exact pattern. These tests are how that claim stays true:
resolution refuses an unapproved pattern without reaching a backend at all, promotion
refuses a safety-gating tool outright rather than raising its bar, promotion refuses
thin evidence, demotion takes effect on the next call, and one tenant's approval never
answers another tenant's question.
"""

from __future__ import annotations

import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from coletar.decisions.backend import DecisionQuery, DecisionResult
from coletar.decisions.promotion import PROMOTION_MIN_OCCURRENCES
from coletar.mcp import rest
from coletar.mcp.auth import ApiKeyAuthenticator, AuthMiddleware
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.tenancy import tenant_id
from coletar.store.memory import InMemoryStore
from conftest import TENANT

OTHER_TENANT = tenant_id("tenant_other")
WRITE_KEY = "sk-integration"
CONSENT_KEY = "sk-operator"
OTHER_KEY = "sk-other"
WRITE_AUTH = {"X-API-Key": WRITE_KEY}
CONSENT_AUTH = {"X-API-Key": CONSENT_KEY}
OTHER_AUTH = {"X-API-Key": OTHER_KEY}

TOOL = "route_ticket"
SITUATION = "enterprise+billing-dispute"
OUTCOME = "route-priority-billing"

KEYS = json.dumps(
    [
        {"id": "integration", "secret": WRITE_KEY, "tenant_id": str(TENANT)},
        {
            "id": "operator",
            "secret": CONSENT_KEY,
            "tenant_id": str(TENANT),
            "scopes": ["read", "write", "consent"],
        },
        {
            "id": "other",
            "secret": OTHER_KEY,
            "tenant_id": str(OTHER_TENANT),
            "scopes": ["read", "write", "consent"],
        },
    ]
)


class StubBackend:
    """Answers confidently. Only ever reachable behind an approved promotion."""

    name = "stub"

    def __init__(self) -> None:
        self.calls: list[DecisionQuery] = []

    async def decide(self, query: DecisionQuery) -> DecisionResult:
        self.calls.append(query)
        return DecisionResult(
            decided=True, outcome_signature=OUTCOME, confidence=0.99, backend=self.name
        )


@pytest.fixture
def store(monkeypatch) -> InMemoryStore:
    backing = InMemoryStore()
    monkeypatch.setattr(rest, "build_store", lambda: backing)
    return backing


@pytest.fixture
def backend(monkeypatch) -> StubBackend:
    stub = StubBackend()

    async def _build(store, tenant_id, tool_name):
        return stub

    monkeypatch.setattr(rest, "build_decision_backend", _build)
    return stub


@pytest.fixture
def client():
    inner = Starlette(routes=[Route(p, e, methods=m) for p, e, m in rest.routes()])
    app = AuthMiddleware(
        inner, ApiKeyAuthenticator.from_config(KEYS), allowed_origins=frozenset()
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _seed_evidence(store, tenant, count: int, *, outcome: str = OUTCOME) -> None:
    """Report `count` identical outcomes straight into the log, as /v1/decisions does."""
    for _ in range(count):
        await store.append_event(
            tenant,
            Event(
                type=EventType.DECISION_OBSERVED,
                actor=Actor.CONNECTOR,
                detail={
                    "tool_name": TOOL,
                    "input_signature": SITUATION,
                    "outcome_signature": outcome,
                },
            ),
        )


async def _resolve(client, *, auth=None):
    return await client.post(
        "/v1/decisions/resolve",
        json={"tool_name": TOOL, "input_signature": SITUATION},
        headers=auth or WRITE_AUTH,
    )


async def _promote(client, *, safety_gating=False, auth=None):
    return await client.post(
        "/v1/decisions/promote",
        json={
            "tool_name": TOOL,
            "input_signature": SITUATION,
            "safety_gating": safety_gating,
        },
        headers=auth or CONSENT_AUTH,
    )


# -- the gate -----------------------------------------------------------------
async def test_an_unpromoted_pattern_never_reaches_the_backend(client, store, backend):
    """The gate is a refusal to ask the question, not a filter on the answer."""
    await _seed_evidence(store, TENANT, PROMOTION_MIN_OCCURRENCES)
    async with client as c:
        response = await _resolve(c)
    assert response.status_code == 200
    assert response.json()["decided"] is False
    assert response.json()["reason"] == "not_promoted"
    assert backend.calls == [], "backend was consulted for an unapproved pattern"


async def test_evidence_alone_promotes_nothing(client, store, backend):
    """A hundred consistent outcomes still need a person to say yes."""
    await _seed_evidence(store, TENANT, PROMOTION_MIN_OCCURRENCES * 4)
    async with client as c:
        response = await _resolve(c)
    assert response.json()["decided"] is False


# -- promotion ----------------------------------------------------------------
async def test_a_safety_gating_tool_is_refused_outright(client, store, backend):
    """Refused, not held to a higher bar: there is no approval path for this class."""
    await _seed_evidence(store, TENANT, PROMOTION_MIN_OCCURRENCES * 2)
    async with client as c:
        response = await _promote(c, safety_gating=True)
    assert response.status_code == 400
    assert response.json()["error"] == "safety_gating_not_promotable"


async def test_thin_evidence_is_refused_with_the_shortfall(client, store, backend):
    await _seed_evidence(store, TENANT, 5)
    async with client as c:
        response = await _promote(c)
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "insufficient_evidence"
    assert body["evidence"] == {
        "occurrences": 5,
        "consistent": True,
        "required": PROMOTION_MIN_OCCURRENCES,
    }


async def test_inconsistent_evidence_is_refused(client, store, backend):
    await _seed_evidence(store, TENANT, PROMOTION_MIN_OCCURRENCES)
    await _seed_evidence(store, TENANT, 1, outcome="route-general")
    async with client as c:
        response = await _promote(c)
    assert response.status_code == 400
    assert response.json()["error"] == "insufficient_evidence"


async def test_promotion_needs_the_consent_scope(client, store, backend):
    await _seed_evidence(store, TENANT, PROMOTION_MIN_OCCURRENCES)
    async with client as c:
        response = await _promote(c, auth=WRITE_AUTH)
    assert response.status_code == 403


async def test_promotion_records_the_evidence_it_was_granted_on(client, store, backend):
    await _seed_evidence(store, TENANT, PROMOTION_MIN_OCCURRENCES)
    async with client as c:
        response = await _promote(c)
    assert response.status_code == 200 and response.json()["promoted"] is True

    promoted = [
        e for e in await store.list_events(TENANT) if e.type is EventType.AUTOMATION_PROMOTED
    ]
    assert len(promoted) == 1
    assert promoted[0].detail["evidence"]["occurrences"] == PROMOTION_MIN_OCCURRENCES
    assert promoted[0].detail["principal"] == "operator"


# -- resolution after approval ------------------------------------------------
async def test_a_promoted_pattern_resolves_and_is_audited(client, store, backend):
    await _seed_evidence(store, TENANT, PROMOTION_MIN_OCCURRENCES)
    async with client as c:
        await _promote(c)
        response = await _resolve(c)

    body = response.json()
    assert body["decided"] is True
    assert body["outcome_signature"] == OUTCOME
    assert body["backend"] == "stub"
    assert len(backend.calls) == 1

    resolved = [
        e
        for e in await store.list_events(TENANT)
        if e.type is EventType.DECISION_OBSERVED and e.detail.get("resolved_by") == "stub"
    ]
    assert len(resolved) == 1
    assert resolved[0].detail["confidence"] == 0.99


async def test_raw_context_is_withheld_without_vendor_consent(client, store, backend):
    """A tenant may consent to capture without consenting to a vendor seeing it."""
    await _seed_evidence(store, TENANT, PROMOTION_MIN_OCCURRENCES)
    async with client as c:
        await _promote(c)
        await c.post(
            "/v1/decisions/resolve",
            json={
                "tool_name": TOOL,
                "input_signature": SITUATION,
                "context": {"account": "acme"},
            },
            headers=WRITE_AUTH,
        )
    assert backend.calls[0].context == {}


async def test_vendor_consent_lets_context_through(client, store, backend):
    await _seed_evidence(store, TENANT, PROMOTION_MIN_OCCURRENCES)
    async with client as c:
        await _promote(c)
        await c.post(
            "/v1/decisions/consent",
            json={"tool_name": TOOL, "consent_type": "vendor_send", "confirm": True},
            headers=CONSENT_AUTH,
        )
        await c.post(
            "/v1/decisions/resolve",
            json={
                "tool_name": TOOL,
                "input_signature": SITUATION,
                "context": {"account": "acme"},
            },
            headers=WRITE_AUTH,
        )
    assert backend.calls[0].context == {"account": "acme"}


# -- the kill switch ----------------------------------------------------------
async def test_demotion_takes_effect_on_the_next_call(client, store, backend):
    await _seed_evidence(store, TENANT, PROMOTION_MIN_OCCURRENCES)
    async with client as c:
        await _promote(c)
        assert (await _resolve(c)).json()["decided"] is True
        demoted = await c.post(
            "/v1/decisions/demote",
            json={"tool_name": TOOL, "input_signature": SITUATION},
            headers=CONSENT_AUTH,
        )
        after = await _resolve(c)
    assert demoted.json() == {"demoted": True}
    assert after.json()["reason"] == "not_promoted"

    events = [
        e for e in await store.list_events(TENANT) if e.type is EventType.AUTOMATION_DEMOTED
    ]
    assert events[0].detail["reason"] == "manual"


# -- fallbacks ----------------------------------------------------------------
async def test_a_failing_backend_falls_back_rather_than_erroring(client, store, monkeypatch):
    """An exception is something a caller can catch and read as permission to proceed."""
    await _seed_evidence(store, TENANT, PROMOTION_MIN_OCCURRENCES)

    async def _explode(store, tenant_id, tool_name):
        raise TimeoutError("vendor did not answer")

    async with client as c:
        await _promote(c)
        monkeypatch.setattr(rest, "build_decision_backend", _explode)
        response = await _resolve(c)
    assert response.status_code == 200
    assert response.json() == {
        "decided": False,
        "outcome_signature": None,
        "confidence": 0.0,
        "backend": "none",
        "reason": "backend_unavailable",
    }


async def test_the_default_backend_declines_every_decision(client, store):
    """With no vendor configured, a promoted pattern still resolves to a fallback."""
    await _seed_evidence(store, TENANT, PROMOTION_MIN_OCCURRENCES)
    async with client as c:
        await _promote(c)
        response = await _resolve(c)
    assert response.json()["decided"] is False
    assert response.json()["reason"] == "no_backend_configured"


# -- tenant isolation ---------------------------------------------------------
async def test_one_tenants_promotion_never_answers_anothers_question(client, store, backend):
    """Signatures are caller-chosen labels, so two tenants collide easily."""
    await _seed_evidence(store, TENANT, PROMOTION_MIN_OCCURRENCES)
    await _seed_evidence(store, OTHER_TENANT, PROMOTION_MIN_OCCURRENCES)
    async with client as c:
        await _promote(c)  # approved for TENANT only
        mine = await _resolve(c, auth=WRITE_AUTH)
        theirs = await _resolve(c, auth=OTHER_AUTH)
    assert mine.json()["decided"] is True
    assert theirs.json()["decided"] is False
    assert theirs.json()["reason"] == "not_promoted"
