"""M1.1 acceptance criteria: the canonical schema and its storage invariants.

Each test here maps to one line of the build plan's M1.1 acceptance list. They are
about the substrate every later milestone reads and writes, so they are deliberately
literal -- a change that breaks one of these is a product change, not a refactor.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from coletar.schema.events import EventType
from coletar.schema.objects import (
    GLOBAL_SCOPE,
    ContextObject,
    Edge,
    EdgeType,
    ExtractionMethod,
    Memory,
    MemoryKind,
    ObjectType,
    OriginType,
    Provenance,
    Provider,
)
from coletar.seed import seed
from coletar.store.memory import InMemoryStore

#: SCOPE §2 lists these by name. If one disappears, the Context Inspector loses a
#: column and the compiler loses a mapping input.
SCOPE_SECTION_2_FIELDS = {
    "id",
    "kind",
    "content",
    "scope",
    "confidence",
    "extraction_method",
    "sensitivity",
    "supersedes",
    "provenance",
    "provider_mappings",
}


def test_memory_carries_every_field_named_in_scope_section_2():
    assert set(Memory.model_fields) >= SCOPE_SECTION_2_FIELDS


@pytest.mark.parametrize("object_type", list(ObjectType))
async def test_every_object_type_round_trips_exactly(object_type: ObjectType):
    """One object of each type, inserted and read back, must match exactly."""
    store = InMemoryStore()
    if object_type is ObjectType.MEMORY:
        original = Memory.from_write("Chris ships on Fridays.", kind=MemoryKind.FACT)
    else:
        original = ContextObject(
            type=object_type,
            content=f"A {object_type} object.",
            extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
            provenance=Provenance(origin_type=OriginType.USER, provider=Provider.COLETAR),
        )

    await store.put_object(original)
    read_back = await store.get_object(original.id)

    assert read_back is not None
    assert read_back.model_dump() == original.model_dump()
    assert type(read_back) is type(original)


async def test_round_trip_survives_the_snapshot_file(tmp_path):
    """The same equality has to hold across serialization, not just in one process."""
    path = tmp_path / "graph.json"
    original = Memory.from_write("Chris runs Postgres on port 5433.", kind=MemoryKind.FACT)
    await InMemoryStore(path).put_object(original)

    read_back = await InMemoryStore(path).get_object(original.id)

    assert read_back is not None
    assert read_back.model_dump() == original.model_dump()


async def test_active_memories_exclude_superseded_objects():
    store = InMemoryStore()
    result = await seed(store)
    original, corrected, current = result.supersedes_chain

    active = {o.id for o in await store.list_objects(type=ObjectType.MEMORY)}

    assert current in active
    assert original not in active
    assert corrected not in active


async def test_superseded_objects_are_still_readable_for_provenance():
    """Never hard-delete: the Inspector has to be able to show what a fact used to
    say, even once something newer replaced it."""
    store = InMemoryStore()
    result = await seed(store)
    original = result.supersedes_chain[0]

    assert await store.get_object(original) is not None
    everything = {o.id for o in await store.list_objects(include_superseded=True, limit=1000)}
    assert original in everything


async def test_object_without_provenance_is_rejected():
    with pytest.raises(ValidationError):
        ContextObject(
            type=ObjectType.MEMORY,
            content="Where did this come from?",
            extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
        )


async def test_object_without_extraction_method_is_rejected():
    """The other half of the provenance promise: an object we cannot explain to a
    user should not exist."""
    with pytest.raises(ValidationError):
        ContextObject(
            type=ObjectType.MEMORY,
            content="How sure are we about this?",
            provenance=Provenance(origin_type=OriginType.USER, provider=Provider.COLETAR),
        )


async def test_duplicate_edge_is_idempotent():
    store = InMemoryStore()
    a = await store.put_object(Memory.from_write("First."))
    b = await store.put_object(Memory.from_write("Second."))
    edge = Edge(src_id=a.id, dst_id=b.id, type=EdgeType.RELATES_TO)

    await store.add_edge(edge)
    await store.add_edge(edge)
    await store.add_edge(Edge(src_id=a.id, dst_id=b.id, type=EdgeType.RELATES_TO))

    assert len(await store.edges_from(a.id)) == 1
    edge_events = [e for e in await store.list_events() if e.type is EventType.EDGE_CREATED]
    assert len(edge_events) == 1


async def test_edges_are_directional_and_queryable_both_ways():
    store = InMemoryStore()
    a = await store.put_object(Memory.from_write("Newer."))
    b = await store.put_object(Memory.from_write("Older."))
    await store.add_edge(Edge(src_id=a.id, dst_id=b.id, type=EdgeType.SUPERSEDES))

    assert [e.dst_id for e in await store.edges_from(a.id)] == [b.id]
    assert [e.src_id for e in await store.edges_to(b.id)] == [a.id]
    assert await store.edges_from(b.id) == []


async def test_seed_populates_one_object_of_every_type_and_a_supersedes_chain():
    store = InMemoryStore()
    result = await seed(store)

    everything = await store.list_objects(include_superseded=True, limit=1000)
    assert {o.type for o in everything} == set(ObjectType)
    assert len(result.supersedes_chain) == 3
    assert GLOBAL_SCOPE in {o.scope for o in everything}
