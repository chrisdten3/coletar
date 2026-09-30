"""The Jev gate and reconcile stages, wired into live sync (docs/DECISION_EVAL.md).

Every test here talks to a mock transport. The questions themselves are tested in
test_jev.py; these are about what the pipeline does with the answers, and above all
about what it does when an answer is missing or unsure.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from coletar.capture import capture_turn, is_pending
from coletar.config import get_settings
from coletar.extraction.decisions import GATE_QUESTION_NAME, RECONCILE_QUESTION_NAME
from coletar.extraction.proposal import (
    Proposal,
    ProposedEntity,
    ProposedFact,
    ProposedMemory,
)
from coletar.ingest import remember
from coletar.jobs.extraction import extract_pending
from coletar.retrieval.embedding import HashingEmbedder
from coletar.schema.events import EventType
from coletar.schema.objects import EdgeType, Memory, MemoryKind, ObjectType, Provider
from coletar.store.memory import InMemoryStore
from conftest import TENANT


@pytest.fixture(autouse=True)
def jev_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("COLETAR_GATE_PROVIDER", "jev")
    monkeypatch.setenv("COLETAR_RECONCILE_PROVIDER", "jev")
    monkeypatch.setenv("COLETAR_JEV_API_KEY", "test-key")
    monkeypatch.setenv("COLETAR_JEV_BASE_URL", "https://jev.test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore(embedder=HashingEmbedder(768))


def _jev(
    answer: Callable[[dict[str, Any]], dict[str, Any] | int], seen: list[dict[str, Any]]
) -> httpx.AsyncClient:
    """A Jev that answers from the request's state, and records every state sent."""

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["state"])
        result = answer(body["state"])
        if isinstance(result, int):
            return httpx.Response(result)
        return httpx.Response(
            200,
            json={"model": "jev-test", "usage": {}, "answers": result},
            headers={"x-typesafe-request-id": "req_t"},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def _relation(label: str, confidence: float) -> dict[str, Any]:
    return {
        RECONCILE_QUESTION_NAME: {
            "type": "choice",
            "choice": label,
            "confidence": confidence,
            "probabilities": {label: confidence},
        }
    }


def _memory(content: str) -> Memory:
    return Memory.from_write(content, kind=MemoryKind.FACT, provider=Provider.CLAUDE)


async def _seed(store: InMemoryStore, content: str) -> str:
    result = await remember(store, TENANT, _memory(content), dedup=False)
    return result.object_id


# -- reconcile ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_confident_supersede_replaces_the_old_memory(store: InMemoryStore) -> None:
    old = await _seed(store, "Lives in Boston")
    seen: list[dict[str, Any]] = []
    client = _jev(lambda _: _relation("supersedes", 0.97), seen)

    candidate = _memory("Lives in Denver since last month")
    result = await remember(store, TENANT, candidate, jev_client=client)

    assert result.created and result.superseded == old
    new = await store.get_object(TENANT, result.object_id)
    assert new is not None and new.supersedes == old
    hits = await store.search(TENANT, "Lives in Boston", top_k=8)
    assert old not in {h.obj.id for h in hits}
    created = [e for e in await store.list_events(TENANT) if e.object_id == new.id]
    assert created[-1].detail["reconcile"]["answers"][0]["label"] == "supersedes"


@pytest.mark.asyncio
async def test_a_confident_duplicate_corroborates(store: InMemoryStore) -> None:
    old = await _seed(store, "Is vegetarian")
    client = _jev(lambda _: _relation("duplicate", 0.95), [])

    candidate = _memory("Is vegetarian and eats no meat")
    result = await remember(store, TENANT, candidate, jev_client=client)

    assert not result.created and result.corroborated == old
    assert len(await store.list_objects(TENANT, type=ObjectType.MEMORY)) == 1


@pytest.mark.asyncio
async def test_an_unsure_supersede_keeps_both_and_flags(store: InMemoryStore) -> None:
    """The dangerous error is a wrong supersede, so below the floor nothing retires."""
    old = await _seed(store, "Studies at Georgetown")
    client = _jev(lambda _: _relation("supersedes", 0.75), [])

    result = await remember(
        store, TENANT, _memory("Is a student at Georgetown University"), jev_client=client
    )

    assert result.created and result.superseded is None and result.flagged == [old]
    new = await store.get_object(TENANT, result.object_id)
    assert new is not None and new.supersedes is None
    assert new.payload["reconcile_flags"] == [
        {"object_id": old, "label": "supersedes", "confidence": 0.75}
    ]
    assert await store.get_object(TENANT, old) is not None


@pytest.mark.asyncio
async def test_a_contradiction_keeps_both_and_links_them(store: InMemoryStore) -> None:
    old = await _seed(store, "Has two siblings")
    client = _jev(lambda _: _relation("contradicts", 0.93), [])

    result = await remember(store, TENANT, _memory("Has no siblings"), jev_client=client)

    assert result.created and result.flagged == [old]
    edges = await store.edges_from(TENANT, result.object_id)
    assert [(e.dst_id, e.type) for e in edges] == [(old, EdgeType.CONTRADICTS)]


@pytest.mark.asyncio
async def test_jev_down_falls_back_to_token_overlap(store: InMemoryStore) -> None:
    old = await _seed(store, "I prefer fixed-point integers over doubles for money.")
    client = _jev(lambda _: 503, [])

    result = await remember(
        store, TENANT, _memory("I prefer fixed-point integers over doubles for money!"),
        jev_client=client,
    )

    assert result.corroborated == old
    corroborated = [
        e for e in await store.list_events(TENANT) if e.type is EventType.OBJECT_CORROBORATED
    ]
    assert corroborated[-1].detail["reconcile"] == {
        "provider": "token_overlap", "fallback_reason": "jev_unavailable"
    }


@pytest.mark.asyncio
async def test_only_a_bounded_set_of_memories_is_sent(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boundary: a few neighbours per candidate, never the graph, never a turn."""
    monkeypatch.setenv("COLETAR_RECONCILE_NEIGHBOURS", "2")
    get_settings.cache_clear()
    for city in ("Boston", "Denver", "Austin", "Seattle"):
        await _seed(store, f"Lives in {city}")
    await capture_turn(store, TENANT, "Lives in Chicago now", surface=Provider.CLAUDE)
    seen: list[dict[str, Any]] = []
    client = _jev(lambda _: _relation("new", 0.99), seen)

    await remember(store, TENANT, _memory("Lives in Chicago"), jev_client=client)

    assert len(seen) == 2
    for state in seen:
        assert set(state) == {"existing_memory", "candidate"}
        assert state["existing_memory"]["statement"].startswith("Lives in ")
        assert state["existing_memory"]["statement"] != "Lives in Chicago now"


@pytest.mark.asyncio
async def test_an_explicit_correction_never_asks_jev(store: InMemoryStore) -> None:
    old = await _seed(store, "Prefers VS Code")
    seen: list[dict[str, Any]] = []
    correction = Memory.from_write("Switched to Zed", provider=Provider.CLAUDE, supersedes=old)

    result = await remember(store, TENANT, correction, jev_client=_jev(lambda _: 500, seen))

    assert result.created and seen == []


@pytest.mark.asyncio
async def test_a_claim_is_never_folded_into_an_entity(store: InMemoryStore) -> None:
    """Entities describe things; only claims are compared, and only claims are sent."""
    from coletar.schema.objects import ContextObject, ExtractionMethod, OriginType, Provenance

    entity = ContextObject(
        type=ObjectType.ENTITY,
        content="Georgetown University, where the user studies",
        extraction_method=ExtractionMethod.MCP_LIVE_WRITE,
        provenance=Provenance(origin_type=OriginType.AGENT, provider=Provider.CLAUDE),
    )
    await store.put_object(TENANT, entity)
    seen: list[dict[str, Any]] = []

    result = await remember(
        store, TENANT, _memory("Studies at Georgetown University"),
        jev_client=_jev(lambda _: _relation("duplicate", 0.99), seen),
    )

    assert result.created and seen == []


@pytest.mark.asyncio
async def test_extracted_facts_are_reconciled(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fact written straight to the store could never retire the one it replaces."""
    monkeypatch.setenv("COLETAR_GATE_PROVIDER", "none")
    get_settings.cache_clear()
    old = await _seed(store, "I am a student at Georgetown")

    async def propose(**_: Any) -> Proposal:
        return Proposal(
            entities=[ProposedEntity(name="Stanford", content="A university")],
            facts=[ProposedFact(content="I am a student at Stanford", about=["Stanford"])],
        )

    monkeypatch.setattr("coletar.extraction.ollama.propose", propose)
    await capture_turn(
        store, TENANT, "I am a student at Stanford now.", surface=Provider.CLAUDE
    )
    seen: list[dict[str, Any]] = []

    await extract_pending(
        store, TENANT, provider="ollama", model="t",
        jev_client=_jev(lambda _: _relation("supersedes", 0.97), seen),
    )

    facts = [
        f for f in await store.list_objects(TENANT, type=ObjectType.FACT)
        if f.content == "I am a student at Stanford"
    ]
    assert len(facts) == 1 and facts[0].supersedes == old
    assert all(s["existing_memory"]["statement"] != "A university" for s in seen)


# -- gate --------------------------------------------------------------------------


def _gate(probability: float) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda _: {GATE_QUESTION_NAME: {"type": "noul", "noul": probability}}


def _extractor(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    async def propose(**kwargs: Any) -> Proposal:
        calls.append(kwargs["transcript"])
        return Proposal(memories=[ProposedMemory(content="Is vegetarian", kind=MemoryKind.FACT)])

    monkeypatch.setattr("coletar.extraction.ollama.propose", propose)


@pytest.mark.asyncio
async def test_a_rejected_turn_never_reaches_the_extractor(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    _extractor(monkeypatch, calls)
    episode = await capture_turn(store, TENANT, "thanks!", surface=Provider.CHATGPT)

    report = await extract_pending(
        store, TENANT, provider="ollama", model="t", jev_client=_jev(_gate(0.05), [])
    )

    assert report.gated_out == 1 and report.processed == 0 and calls == []
    done = await store.get_object(TENANT, episode.id)
    assert done is not None and not is_pending(done)
    assert done.payload["gate"]["status"] == "rejected"
    assert done.payload["gate"]["probability"] == 0.05


@pytest.mark.asyncio
async def test_a_passed_turn_is_extracted_and_says_so(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    _extractor(monkeypatch, calls)
    episode = await capture_turn(store, TENANT, "I am vegetarian.", surface=Provider.CHATGPT)
    seen: list[dict[str, Any]] = []

    report = await extract_pending(
        store, TENANT, provider="ollama", model="t", jev_client=_jev(_gate(0.4), seen)
    )

    assert report.processed == 1 and calls == ["I am vegetarian."]
    assert seen[0] == {"previous_turn": None, "latest_turn": "I am vegetarian."}
    done = await store.get_object(TENANT, episode.id)
    assert done is not None and done.payload["gate"]["status"] == "passed"


@pytest.mark.asyncio
async def test_the_gate_fails_open(store: InMemoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected turn is never looked at again, so an outage must not reject."""
    calls: list[str] = []
    _extractor(monkeypatch, calls)
    episode = await capture_turn(store, TENANT, "I am vegetarian.", surface=Provider.CHATGPT)

    report = await extract_pending(
        store, TENANT, provider="ollama", model="t", jev_client=_jev(lambda _: 503, [])
    )

    assert report.processed == 1 and report.gated_out == 0 and len(calls) == 1
    done = await store.get_object(TENANT, episode.id)
    assert done is not None
    assert done.payload["gate"] == {
        "provider": "jev", "status": "unavailable", "reason": "JevUnavailable"
    }
