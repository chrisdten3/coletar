"""`POST /v1/decisions` and `history.patterns.find_repeat_decisions`.

Not a browser bridge endpoint: no Origin check, called directly from a caller's own
backend code the way a Datadog client is, right after their tool ran.
"""

from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from coletar.history.patterns import find_repeat_decisions
from coletar.mcp import rest
from coletar.mcp.auth import ApiKeyAuthenticator, AuthMiddleware
from coletar.schema.events import EventType
from coletar.store.memory import InMemoryStore
from conftest import TENANT

KEY = "sk-decisions"
AUTH = {"X-API-Key": KEY}


@pytest.fixture
def store(monkeypatch) -> InMemoryStore:
    backing = InMemoryStore()
    monkeypatch.setattr(rest, "build_store", lambda: backing)
    return backing


@pytest.fixture
def client():
    import json

    keys = json.dumps([{"id": "reporter", "secret": KEY, "tenant_id": str(TENANT)}])
    inner = Starlette(routes=[Route(p, e, methods=m) for p, e, m in rest.routes()])
    app = AuthMiddleware(inner, ApiKeyAuthenticator.from_config(keys), allowed_origins=frozenset())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def _report(client, *, tool_name="route_ticket", input_signature="enterprise-billing",
                   outcome_signature="route-priority"):
    return await client.post(
        "/v1/decisions",
        json={
            "tool_name": tool_name,
            "input_signature": input_signature,
            "outcome_signature": outcome_signature,
        },
        headers=AUTH,
    )


async def test_a_reported_decision_is_recorded(client, store):
    async with client as c:
        response = await _report(c)
    assert response.status_code == 200 and response.json()["recorded"] is True

    events = await store.list_events(TENANT)
    assert events[0].type is EventType.DECISION_OBSERVED
    assert events[0].detail["tool_name"] == "route_ticket"
    assert events[0].detail["outcome_signature"] == "route-priority"


async def test_unauthenticated_calls_are_rejected(client, store):
    async with client as c:
        response = await c.post("/v1/decisions", json={
            "tool_name": "x", "input_signature": "y", "outcome_signature": "z",
        })
    assert response.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"tool_name": "", "input_signature": "y", "outcome_signature": "z"},
        {"tool_name": "x", "input_signature": "y", "outcome_signature": "z" * 500},
        {"tool_name": "x", "input_signature": "y"},
    ],
)
async def test_malformed_reports_are_rejected_cleanly(client, store, body):
    async with client as c:
        response = await c.post("/v1/decisions", json=body, headers=AUTH)
    assert response.status_code == 400


async def test_a_consistent_outcome_is_flagged_as_a_pattern(client, store):
    async with client as c:
        for _ in range(3):
            await _report(c)

    decisions = await find_repeat_decisions(store, TENANT)
    assert len(decisions) == 1
    assert decisions[0].tool_name == "route_ticket"
    assert decisions[0].occurrences == 3
    assert decisions[0].consistent is True
    assert decisions[0].last_outcome == "route-priority"


async def test_an_inconsistent_outcome_is_not_flagged_as_consistent(client, store):
    async with client as c:
        await _report(c, outcome_signature="route-priority")
        await _report(c, outcome_signature="route-general")
        await _report(c, outcome_signature="route-priority")

    decisions = await find_repeat_decisions(store, TENANT)
    assert len(decisions) == 1
    assert decisions[0].consistent is False


async def test_below_the_occurrence_floor_is_not_a_pattern(client, store):
    async with client as c:
        await _report(c)
        await _report(c)

    assert await find_repeat_decisions(store, TENANT) == []


async def test_different_situations_are_not_merged(client, store):
    async with client as c:
        for _ in range(3):
            await _report(c, input_signature="enterprise-billing")
        for _ in range(3):
            await _report(c, input_signature="smb-billing", outcome_signature="route-general")

    decisions = await find_repeat_decisions(store, TENANT)
    assert {d.input_signature for d in decisions} == {"enterprise-billing", "smb-billing"}
