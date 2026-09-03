"""Capture keeps the turn; extraction decides about it later."""

from __future__ import annotations

import pytest

from coletar.capture import PENDING, capture_turn, is_pending
from coletar.schema.objects import LocalityMode, ObjectType, Provider
from coletar.store.memory import InMemoryStore
from conftest import TENANT


@pytest.mark.asyncio
async def test_a_captured_turn_is_stored_verbatim() -> None:
    store = InMemoryStore()
    text = "I run npm test and it fails on the auth suite, any idea why?"
    episode = await capture_turn(store, TENANT, text, surface=Provider.CHATGPT)

    assert episode.type is ObjectType.EPISODE
    assert episode.content == text, "verbatim — the batch pass re-reads this"
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


@pytest.mark.asyncio
async def test_the_turn_is_kept_even_when_the_heuristic_finds_nothing() -> None:
    """The whole point. A turn the heuristic missed is exactly what the batch pass
    exists to catch, so capturing only the successes would defeat the design."""
    from coletar.extraction import extract_memories

    text = "I run npm test and it fails on the auth suite, any idea why?"
    assert await extract_memories(user_text=text) == [], "heuristic finds nothing here"

    store = InMemoryStore()
    await capture_turn(store, TENANT, text, surface=Provider.CHATGPT)
    kept = await store.list_objects(TENANT, type=ObjectType.EPISODE, caller_surface=Provider.CHATGPT)
    assert len(kept) == 1


@pytest.mark.asyncio
async def test_an_episode_the_model_has_seen_is_no_longer_pending() -> None:
    store = InMemoryStore()
    episode = await capture_turn(store, TENANT, "anything", surface=Provider.CHATGPT)
    episode.payload = {**episode.payload, PENDING: False}
    assert not is_pending(episode)
