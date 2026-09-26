"""Capture keeps the turn; extraction decides about it later."""

from __future__ import annotations

import pytest

from coletar.capture import PENDING, capture_turn, is_pending
from coletar.episode_crypto import PREFIX, decrypt_episode
from coletar.schema.objects import LocalityMode, ObjectType, Provider
from coletar.store.memory import InMemoryStore
from conftest import TENANT


@pytest.mark.asyncio
async def test_a_captured_turn_is_stored_losslessly_but_not_in_plaintext() -> None:
    store = InMemoryStore()
    text = "I run npm test and it fails on the auth suite, any idea why?"
    episode = await capture_turn(store, TENANT, text, surface=Provider.CHATGPT)

    assert episode.type is ObjectType.EPISODE
    assert episode.content.startswith(PREFIX)
    assert text not in episode.content
    assert await decrypt_episode(store, TENANT, episode) == text
    assert is_pending(episode), "and is queued for the model pass"


@pytest.mark.asyncio
async def test_a_captured_turn_carries_a_ttl() -> None:
    """An episode without one would outlive every retention promise the product
    makes. `coletar expire` is what reaches it."""
    store = InMemoryStore()
    episode = await capture_turn(store, TENANT, "anything", surface=Provider.CHATGPT)
    assert episode.ttl_days is not None and episode.ttl_days > 0


@pytest.mark.asyncio
async def test_a_captured_turn_is_readable_only_by_the_surface_it_came_from() -> None:
    """A raw turn is not a memory the user chose to keep and share — it is working
    material. Defaulting it to every surface would put text typed into one assistant
    in front of another before any human reviewed it."""
    store = InMemoryStore()
    episode = await capture_turn(store, TENANT, "something private", surface=Provider.CLAUDE)

    assert episode.locality.mode is LocalityMode.LOCAL_ONLY
    assert episode.locality.surfaces == frozenset({Provider.CLAUDE})

    seen_by_chatgpt = await store.list_objects(TENANT, caller_surface=Provider.CHATGPT)
    assert episode.id not in {o.id for o in seen_by_chatgpt}


@pytest.mark.asyncio
async def test_capture_appends_an_event() -> None:
    """§5: nothing mutates the graph without one."""
    store = InMemoryStore()
    episode = await capture_turn(store, TENANT, "anything", surface=Provider.CHATGPT)
    events = await store.list_events(TENANT, object_id=episode.id)
    assert events, "a write with no event is a silent data-integrity failure"
    assert "anything" not in str([event.model_dump(mode="json") for event in events])


@pytest.mark.asyncio
async def test_a_raw_episode_never_appears_in_normal_retrieval() -> None:
    store = InMemoryStore()
    episode = await capture_turn(
        store, TENANT, "private launch codename albatross", surface=Provider.CHATGPT
    )

    hits = await store.search(TENANT, "private launch codename albatross", top_k=50)
    assert episode.id not in {hit.obj.id for hit in hits}


@pytest.mark.asyncio
async def test_the_turn_is_kept_even_when_the_heuristic_finds_nothing() -> None:
    """The whole point. A turn the heuristic missed is exactly what the batch pass
    exists to catch, so capturing only the successes would defeat the design."""
    from coletar.extraction import extract_memories

    text = "I run npm test and it fails on the auth suite, any idea why?"
    assert await extract_memories(user_text=text) == [], "heuristic finds nothing here"

    store = InMemoryStore()
    await capture_turn(store, TENANT, text, surface=Provider.CHATGPT)
    kept = await store.list_objects(
        TENANT, type=ObjectType.EPISODE, caller_surface=Provider.CHATGPT
    )
    assert len(kept) == 1


@pytest.mark.asyncio
async def test_an_episode_the_model_has_seen_is_no_longer_pending() -> None:
    store = InMemoryStore()
    episode = await capture_turn(store, TENANT, "anything", surface=Provider.CHATGPT)
    episode.payload = {**episode.payload, PENDING: False}
    assert not is_pending(episode)


@pytest.mark.asyncio
async def test_identified_turn_retries_do_not_replace_keys_or_append_events() -> None:
    store = InMemoryStore()
    kwargs = dict(surface=Provider.CLAUDE, turn_id="send-1", conversation_id="chat-1")
    first = await capture_turn(store, TENANT, "Same prompt", **kwargs)
    first_key = await store.get_object_key(TENANT, first.id)
    second = await capture_turn(store, TENANT, "Same prompt", **kwargs)
    assert first.id == second.id
    assert await store.get_object_key(TENANT, first.id) == first_key
    assert len(await store.list_events(TENANT, object_id=first.id)) == 1


@pytest.mark.asyncio
async def test_reusing_turn_id_for_changed_content_is_rejected() -> None:
    from coletar.capture import CaptureConflict

    store = InMemoryStore()
    await capture_turn(store, TENANT, "Original", surface=Provider.CLAUDE, turn_id="send-1")
    with pytest.raises(CaptureConflict):
        await capture_turn(store, TENANT, "Changed", surface=Provider.CLAUDE, turn_id="send-1")


@pytest.mark.asyncio
async def test_assistant_evidence_is_not_user_memory_or_extraction_input() -> None:
    from coletar.schema.objects import ExtractionMethod, OriginType

    store = InMemoryStore()
    user = await capture_turn(store, TENANT, "Question", surface=Provider.CLAUDE, turn_id="s1")
    reply = await capture_turn(
        store,
        TENANT,
        "I live in Paris",
        surface=Provider.CLAUDE,
        turn_id="s1",
        role="assistant",
    )
    assert user.id != reply.id
    assert reply.provenance.origin_type is OriginType.AGENT
    assert reply.extraction_method is ExtractionMethod.BROWSER_CAPTURE
    assert not is_pending(reply)
    reply.payload[PENDING] = True
    assert not is_pending(reply), "even an incorrect pending flag cannot mine model text"
    assert await decrypt_episode(store, TENANT, reply) == "I live in Paris"


@pytest.mark.asyncio
async def test_delayed_retry_does_not_resurrect_erased_turn() -> None:
    store = InMemoryStore()
    obj = await capture_turn(store, TENANT, "Private", surface=Provider.CLAUDE, turn_id="s1")
    await store.shred_object_key(TENANT, obj.id, reason="user erasure")
    retry = await capture_turn(store, TENANT, "Private", surface=Provider.CLAUDE, turn_id="s1")
    assert retry.id == obj.id
    assert await store.get_object_key(TENANT, obj.id) is None


@pytest.mark.asyncio
async def test_repeated_text_in_distinct_turns_is_not_dropped() -> None:
    store = InMemoryStore()
    first = await capture_turn(store, TENANT, "Continue", surface=Provider.CLAUDE, turn_id="s1")
    second = await capture_turn(store, TENANT, "Continue", surface=Provider.CLAUDE, turn_id="s2")
    assert first.id != second.id


@pytest.mark.asyncio
async def test_capture_lease_rejects_concurrent_delivery() -> None:
    from coletar.capture import CaptureBusy

    store = InMemoryStore()
    obj = await capture_turn(store, TENANT, "Text", surface=Provider.CLAUDE, turn_id="s1")
    await store.acquire_lease(TENANT, "capture:" + obj.id, owner="another", ttl_seconds=120)
    with pytest.raises(CaptureBusy):
        await capture_turn(store, TENANT, "Text", surface=Provider.CLAUDE, turn_id="s1")
