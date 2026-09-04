"""Retention that retains, and entity identity that survives a second import."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from coletar.capture import capture_turn
from coletar.episode_crypto import EpisodeKeyUnavailable, decrypt_episode
from coletar.jobs.expiry import REASON, expire, expires_at
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    ContextObject,
    ExtractionMethod,
    Memory,
    MemoryKind,
    ObjectType,
    OriginType,
    Provenance,
    Provider,
)
from coletar.store.memory import InMemoryStore
from conftest import TENANT


def _event(object_id: str) -> Event:
    return Event(
        type=EventType.CONNECTOR_WRITE,
        object_id=object_id,
        actor=Actor.MIGRATION,
        provider=Provider.COLETAR,
    )


async def _put(store: InMemoryStore, obj: ContextObject) -> ContextObject:
    await store.put_object(TENANT, obj, event=_event(obj.id))
    return obj


@pytest.mark.asyncio
async def test_an_expired_object_is_retired_not_deleted() -> None:
    """Constraint 6 holds even here, where deletion sounds most reasonable. A user
    must still be able to see what a fact used to say and when it stopped applying,
    and an object that vanishes cannot explain its own absence."""
    store = InMemoryStore()
    obj = Memory.from_write("I am on the trial plan", kind=MemoryKind.FACT)
    obj.ttl_days = 30
    obj.created_at = datetime.now(UTC) - timedelta(days=31)
    await _put(store, obj)

    report = await expire(store, TENANT)
    assert report.retired == 1

    assert await store.list_objects(TENANT) == [], "expired objects leave retrieval"
    still_there = await store.get_object(TENANT, obj.id)
    assert still_there is not None, "and remain readable for provenance"
    assert still_there.retired_at is not None


@pytest.mark.asyncio
async def test_an_object_inside_its_ttl_is_untouched() -> None:
    store = InMemoryStore()
    obj = Memory.from_write("I prefer tabs", kind=MemoryKind.PREFERENCE)
    obj.ttl_days = 30
    obj.created_at = datetime.now(UTC) - timedelta(days=29)
    await _put(store, obj)

    assert (await expire(store, TENANT)).retired == 0
    assert len(await store.list_objects(TENANT)) == 1


@pytest.mark.asyncio
async def test_an_object_with_no_ttl_never_expires() -> None:
    """`ttl_days = None` means "as far as we know, always" — the honest reading of
    an undated preference, and the default for every object written today."""
    store = InMemoryStore()
    await _put(store, Memory.from_write("I prefer tabs", kind=MemoryKind.PREFERENCE))
    assert (await expire(store, TENANT)).retired == 0


@pytest.mark.asyncio
async def test_expiry_uses_the_same_clock_the_dashboard_shows() -> None:
    """A job expiring on a different clock from the countdown the Inspector renders
    is a bug the user finds before we do."""
    from coletar.inspector.metrics import _expires_at

    obj = Memory.from_write("x", kind=MemoryKind.FACT)
    obj.ttl_days = 7
    assert expires_at(obj) == _expires_at(obj)


@pytest.mark.asyncio
async def test_the_retirement_reason_distinguishes_expiry_from_supersession() -> None:
    """Two different stories for the user: this stopped applying, versus something
    newer replaced it."""
    store = InMemoryStore()
    obj = Memory.from_write("I am on the trial plan", kind=MemoryKind.FACT)
    obj.ttl_days = 1
    obj.created_at = datetime.now(UTC) - timedelta(days=2)
    await _put(store, obj)
    await expire(store, TENANT)

    events = await store.list_events(TENANT, object_id=obj.id)
    assert any(REASON in str(e.detail) for e in events), "the event says why"


@pytest.mark.asyncio
async def test_expired_raw_episode_is_crypto_shredded() -> None:
    store = InMemoryStore()
    episode = await capture_turn(
        store, TENANT, "erase this raw turn", surface=Provider.CHATGPT
    )
    episode.created_at = datetime.now(UTC) - timedelta(days=episode.ttl_days or 1, seconds=1)
    await store.put_object(TENANT, episode)

    await expire(store, TENANT)

    encrypted = await store.get_object(TENANT, episode.id)
    assert encrypted is not None, "the graph and provenance remain"
    with pytest.raises(EpisodeKeyUnavailable):
        await decrypt_episode(store, TENANT, encrypted)
    events = await store.list_events(TENANT, object_id=episode.id)
    assert any(event.type is EventType.OBJECT_SHREDDED for event in events)


def _entity(name: str) -> ContextObject:
    return ContextObject(
        type=ObjectType.ENTITY,
        content=f"{name}, from a conversation",
        extraction_method=ExtractionMethod.ACCOUNT_EXPORT_PARSE,
        provenance=Provenance(
            origin_type=OriginType.USER, provider=Provider.CHATGPT, confidence=0.6
        ),
        payload={"name": name},
    )


@pytest.mark.asyncio
async def test_an_entity_is_found_by_name_across_imports() -> None:
    """Per-import dedup is not enough: a second import of the same corpus would
    otherwise create a second Amanda, and continuous capture makes that permanent
    rather than per-file."""
    store = InMemoryStore()
    amanda = await _put(store, _entity("Amanda"))

    assert (await store.find_entity(TENANT, "Amanda")).id == amanda.id
    assert (await store.find_entity(TENANT, "  amanda  ")).id == amanda.id, "casefolded"
    assert await store.find_entity(TENANT, "Dana") is None


@pytest.mark.asyncio
async def test_a_retired_entity_is_not_matched() -> None:
    """Otherwise deleting a person would silently re-link new facts to the object
    the user retired."""
    store = InMemoryStore()
    amanda = await _put(store, _entity("Amanda"))
    await store.retire_object(TENANT, amanda.id, reason="user deleted")
    assert await store.find_entity(TENANT, "Amanda") is None


@pytest.mark.asyncio
async def test_entity_lookup_is_tenant_scoped() -> None:
    store = InMemoryStore()
    await _put(store, _entity("Amanda"))
    assert await store.find_entity("tenant_other", "Amanda") is None
