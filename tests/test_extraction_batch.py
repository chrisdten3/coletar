"""Captured turns are model-extracted later, exactly once from the user's view."""

from __future__ import annotations

import pytest

from coletar.capture import capture_turn, is_pending
from coletar.extraction.proposal import Proposal, ProposedMemory
from coletar.extraction.providers import ExtractionUnavailable
from coletar.jobs.extraction import extract_pending
from coletar.schema.objects import MemoryKind, ObjectType, Provider
from coletar.store.memory import InMemoryStore
from conftest import TENANT


@pytest.mark.asyncio
async def test_batch_materialises_and_acknowledges_a_captured_turn(monkeypatch) -> None:
    store = InMemoryStore()
    episode = await capture_turn(
        store, TENANT, "I am vegetarian.", surface=Provider.CHATGPT
    )

    async def _proposal(**kwargs: object) -> Proposal:
        assert kwargs["transcript"] == "I am vegetarian."
        return Proposal(
            memories=[ProposedMemory(content="I am vegetarian", kind=MemoryKind.FACT)]
        )

    monkeypatch.setattr("coletar.extraction.ollama.propose", _proposal)
    report = await extract_pending(store, TENANT, provider="ollama", model="test")

    assert report.processed == 1
    updated = await store.get_object(TENANT, episode.id)
    assert updated is not None and not is_pending(updated)
    assert updated.payload["extraction_provider"] == "ollama"
    assert updated.payload["extraction_model"] == "test"
    memories = await store.list_objects(TENANT, type=ObjectType.MEMORY)
    assert len(memories) == 1
    assert memories[0].provenance.source_object_ids == [episode.id]
    assert memories[0].locality.mode.value == "synced"


@pytest.mark.asyncio
async def test_unavailable_turn_stays_pending(monkeypatch) -> None:
    store = InMemoryStore()
    episode = await capture_turn(store, TENANT, "anything", surface=Provider.CHATGPT)

    async def _unavailable(**_: object) -> Proposal:
        raise ExtractionUnavailable("busy")

    monkeypatch.setattr("coletar.extraction.ollama.propose", _unavailable)
    report = await extract_pending(store, TENANT, provider="ollama", model="test")

    assert report.unavailable == 1
    unchanged = await store.get_object(TENANT, episode.id)
    assert unchanged is not None and is_pending(unchanged)


@pytest.mark.asyncio
async def test_retry_does_not_duplicate_objects(monkeypatch) -> None:
    store = InMemoryStore()
    await capture_turn(store, TENANT, "I am vegetarian.", surface=Provider.CHATGPT)

    async def _proposal(**_: object) -> Proposal:
        return Proposal(
            memories=[ProposedMemory(content="I am vegetarian", kind=MemoryKind.FACT)]
        )

    monkeypatch.setattr("coletar.extraction.ollama.propose", _proposal)
    await extract_pending(store, TENANT, provider="ollama", model="test")
    second = await extract_pending(store, TENANT, provider="ollama", model="test")

    assert second.processed == 0
    assert len(await store.list_objects(TENANT, type=ObjectType.MEMORY)) == 1


@pytest.mark.asyncio
async def test_crash_after_object_write_retries_without_a_duplicate(monkeypatch) -> None:
    from coletar.schema.objects import ContextObject

    class FailAcknowledgementOnce(InMemoryStore):
        fail = False

        async def put_object(self, tenant_id, obj: ContextObject, *, event=None):
            if self.fail and obj.type is ObjectType.EPISODE:
                self.fail = False
                raise RuntimeError("crash before queue acknowledgement")
            return await super().put_object(tenant_id, obj, event=event)

    store = FailAcknowledgementOnce()
    await capture_turn(store, TENANT, "I am vegetarian.", surface=Provider.CHATGPT)

    async def _proposal(**_: object) -> Proposal:
        return Proposal(
            memories=[ProposedMemory(content="I am vegetarian", kind=MemoryKind.FACT)]
        )

    monkeypatch.setattr("coletar.extraction.ollama.propose", _proposal)
    store.fail = True
    with pytest.raises(RuntimeError, match="queue acknowledgement"):
        await extract_pending(store, TENANT, provider="ollama", model="test")

    assert len(await store.list_objects(TENANT, type=ObjectType.MEMORY)) == 1
    await extract_pending(store, TENANT, provider="ollama", model="test")
    assert len(await store.list_objects(TENANT, type=ObjectType.MEMORY)) == 1
