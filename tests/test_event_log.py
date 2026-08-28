"""M1.3 acceptance criteria: the append-only Event/Revision Log.

The log is the provenance record, the observability feed (§6) and the staleness
input to the Continuity Score (§7). Everything here is about it being trustworthy:
complete, unmodifiable, and sufficient on its own to reconstruct the past.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import Edge, EdgeType, Memory, MemoryKind
from coletar.store.memory import InMemoryStore
from coletar.store.replay import replay_history, replay_object
from conftest import TENANT


async def test_create_writes_exactly_one_row():
    store = InMemoryStore()
    await store.put_object(TENANT, Memory.from_write("A durable fact."))
    events = await store.list_events(TENANT)
    assert len(events) == 1
    assert events[0].type is EventType.OBJECT_CREATED


async def test_update_writes_exactly_one_row():
    store = InMemoryStore()
    obj = await store.put_object(TENANT, Memory.from_write("Chris uses pip."))
    obj.content = "Chris uses uv."
    await store.put_object(TENANT, obj)

    events = await store.list_events(TENANT)
    assert len(events) == 2
    assert events[0].type is EventType.OBJECT_UPDATED


async def test_a_read_object_cannot_mutate_the_graph_behind_the_log():
    """The graph may only change through a write that appends an event. Handing out
    live references would make a silent mutation possible, so reads are detached."""
    store = InMemoryStore()
    obj = await store.put_object(TENANT, Memory.from_write("The stored wording."))

    fetched = await store.get_object(TENANT, obj.id)
    assert fetched is not None
    fetched.content = "Mutated without an event."

    unchanged = await store.get_object(TENANT, obj.id)
    assert unchanged is not None and unchanged.content == "The stored wording."
    assert len(await store.list_events(TENANT, object_id=obj.id)) == 1


async def test_retire_writes_exactly_one_row():
    store = InMemoryStore()
    obj = await store.put_object(TENANT, Memory.from_write("Superseded soon."))
    await store.retire_object(TENANT, obj.id, reason="superseded")

    events = await store.list_events(TENANT, object_id=obj.id)
    assert [e.type for e in events] == [EventType.OBJECT_RETIRED, EventType.OBJECT_CREATED]


async def test_edge_creation_writes_exactly_one_row():
    store = InMemoryStore()
    a = await store.put_object(TENANT, Memory.from_write("One."))
    b = await store.put_object(TENANT, Memory.from_write("Two."))
    await store.add_edge(TENANT, Edge(src_id=a.id, dst_id=b.id, type=EdgeType.RELATES_TO))

    edge_events = [e for e in await store.list_events(TENANT) if e.type is EventType.EDGE_CREATED]
    assert len(edge_events) == 1


async def test_write_event_captures_before_and_after_state():
    store = InMemoryStore()
    obj = await store.put_object(TENANT, Memory.from_write("Chris works at Acme."))
    obj.content = "Chris works at Globex."
    await store.put_object(TENANT, obj)

    created, updated = (await store.list_events(TENANT, object_id=obj.id))[::-1]
    assert created.before is None
    assert created.after is not None and created.after["content"] == "Chris works at Acme."
    assert updated.before is not None and updated.before["content"] == "Chris works at Acme."
    assert updated.after is not None and updated.after["content"] == "Chris works at Globex."


async def test_a_caller_supplied_event_still_gets_before_and_after():
    """The MCP server tags its writes `connector.write`. Supplying an event must not
    be a way to end up with an unreplayable log row."""
    store = InMemoryStore()
    memory = Memory.from_write("Stated in a live conversation.")
    await store.put_object(TENANT, 
        memory,
        event=Event(type=EventType.CONNECTOR_WRITE, object_id=memory.id, actor=Actor.MODEL),
    )

    event = (await store.list_events(TENANT, object_id=memory.id))[0]
    assert event.type is EventType.CONNECTOR_WRITE
    assert event.actor is Actor.MODEL
    assert event.after is not None and event.after["id"] == memory.id


def test_an_event_cannot_be_modified_after_creation():
    event = Event(type=EventType.OBJECT_CREATED, object_id="mem_1")
    with pytest.raises(ValidationError):
        event.type = EventType.OBJECT_RETIRED
    with pytest.raises(ValidationError):
        event.object_id = "mem_2"


async def test_reading_the_log_hands_out_copies():
    """A caller that mutates what it was given must not be able to rewrite history."""
    store = InMemoryStore()
    obj = await store.put_object(TENANT, Memory.from_write("The original wording."))

    handed_out = (await store.list_events(TENANT, object_id=obj.id))[0]
    assert handed_out.after is not None
    handed_out.after["content"] = "Tampered."

    again = (await store.list_events(TENANT, object_id=obj.id))[0]
    assert again.after is not None
    assert again.after["content"] == "The original wording."


async def test_replay_reproduces_state_as_of_a_past_timestamp():
    store = InMemoryStore()
    obj = await store.put_object(
        TENANT, Memory.from_write("Chris works at Acme.", kind=MemoryKind.FACT))
    v1 = obj.model_dump(mode="json")
    await asyncio.sleep(0.01)
    after_v1 = datetime.now(UTC)

    await asyncio.sleep(0.01)
    obj.content = "Chris works at Globex."
    # put_object does not mutate its argument, so the stored state is what it
    # returns -- the caller's copy is still at version 1.
    v2 = (await store.put_object(TENANT, obj)).model_dump(mode="json")
    await asyncio.sleep(0.01)
    after_v2 = datetime.now(UTC)

    await asyncio.sleep(0.01)
    await store.retire_object(TENANT, obj.id, reason="superseded")

    assert (await replay_object(store, TENANT, obj.id, at=after_v1)).model_dump(mode="json") == v1
    assert (await replay_object(store, TENANT, obj.id, at=after_v2)).model_dump(mode="json") == v2

    now_state = await replay_object(store, TENANT, obj.id)
    assert now_state is not None and not now_state.is_active
    # Replay reads only the log, so it must agree with the object table.
    live = await store.get_object(TENANT, obj.id)
    assert live is not None
    assert now_state.model_dump(mode="json") == live.model_dump(mode="json")


async def test_replay_before_an_object_existed_returns_nothing():
    store = InMemoryStore()
    before_anything = datetime.now(UTC)
    await asyncio.sleep(0.01)
    obj = await store.put_object(TENANT, Memory.from_write("Later."))

    assert await replay_object(store, TENANT, obj.id, at=before_anything) is None


async def test_replay_history_is_the_inspector_timeline():
    store = InMemoryStore()
    obj = await store.put_object(TENANT, Memory.from_write("Draft one."))
    obj.content = "Draft two."
    await store.put_object(TENANT, obj)
    await store.retire_object(TENANT, obj.id, reason="compressed")

    history = await replay_history(store, TENANT, obj.id)

    assert [r.state.content for r in history] == ["Draft one.", "Draft two.", "Draft two."]
    assert [r.event.type for r in history] == [
        EventType.OBJECT_CREATED,
        EventType.OBJECT_UPDATED,
        EventType.OBJECT_RETIRED,
    ]
    assert history == sorted(history, key=lambda r: r.at)


async def test_access_events_are_not_revisions():
    """Reading is not a revision -- otherwise replay would report a search as an
    edit, and the Inspector timeline would be mostly noise."""
    store = InMemoryStore()
    obj = await store.put_object(TENANT, Memory.from_write("Read me."))
    await store.append_event(TENANT, 
        Event(type=EventType.OBJECT_ACCESSED, object_id=obj.id, actor=Actor.MODEL)
    )

    assert len(await replay_history(store, TENANT, obj.id)) == 1


async def test_logging_overhead_stays_under_10ms_per_write():
    """Bound the log write itself. The snapshot rewrite in the JSON-backed store is
    a documented development convenience, not the M1 backend, so it is excluded
    here by giving the store no snapshot path."""
    store = InMemoryStore()
    durations: list[float] = []
    for i in range(200):
        event = Event(type=EventType.OBJECT_CREATED, object_id=f"mem_{i}")
        start = time.perf_counter()
        await store.append_event(TENANT, event)
        durations.append((time.perf_counter() - start) * 1000)

    durations.sort()
    assert durations[int(0.95 * len(durations)) - 1] < 10.0, statistics.mean(durations)
