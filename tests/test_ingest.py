"""Write-time deduplication at the ingest boundary.

Near-duplicates were already dropped at *assembly* time, which protects retrieval —
the model never sees the same fact twice. It does not protect the compiler: a compile
reads `list_objects`, so True Migration would faithfully emit every duplicate the
proxy ever wrote, into a destination with finite context. These tests are about the
path the compiler reads, not the path the model reads.
"""

from __future__ import annotations

import pytest

from coletar.ingest import find_duplicate, is_near_duplicate, remember
from coletar.retrieval import retrieve
from coletar.retrieval.embedding import HashingEmbedder
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import Memory, MemoryKind, Scope, ScopeType
from coletar.store.memory import InMemoryStore
from conftest import TENANT

STATEMENT = "I prefer fixed-point integers over doubles for money."
RESTATED = "I prefer fixed-point integers over doubles for money!"


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore(embedder=HashingEmbedder(768))


# -- the sameness test --------------------------------------------------------
def test_sameness_matches_the_assembly_stage():
    """One definition of near-duplicate, so a fact judged a duplicate at read time is
    judged a duplicate at write time."""
    assert is_near_duplicate(STATEMENT, RESTATED)
    assert not is_near_duplicate(STATEMENT, "Chris drinks his coffee black.")
    assert not is_near_duplicate("", STATEMENT)


# -- the compiler's view ------------------------------------------------------
async def test_a_restated_fact_does_not_grow_the_graph(store):
    first = await remember(store, TENANT, Memory.from_write(STATEMENT))
    second = await remember(store, TENANT, Memory.from_write(RESTATED))

    assert first.created and not second.created
    assert second.corroborated == first.object_id
    # The list the compiler reads holds one object, not two.
    assert len(await store.list_objects(TENANT, limit=100)) == 1


async def test_ten_restatements_still_compile_to_one_object(store):
    for _ in range(10):
        await remember(store, TENANT, Memory.from_write(STATEMENT))

    assert len(await store.list_objects(TENANT, limit=100)) == 1


async def test_a_duplicate_corroborates_rather_than_vanishing(store):
    """That the user said it again is real provenance. Dropping it silently throws
    that away."""
    first = await remember(store, TENANT, Memory.from_write(STATEMENT))
    await remember(store, TENANT, Memory.from_write(RESTATED))

    events = await store.list_events(TENANT, object_id=first.object_id)
    corroborations = [e for e in events if e.type is EventType.OBJECT_CORROBORATED]
    assert len(corroborations) == 1
    assert corroborations[0].detail["restated_as"] == RESTATED


async def test_confidence_is_not_inflated_by_repetition(store):
    """Repetition is weak evidence. Bumping a score on it is arithmetic nobody
    asked for, and it would quietly reorder retrieval."""
    first = await remember(store, TENANT, Memory.from_write(STATEMENT))
    before = await store.get_object(TENANT, first.object_id)
    for _ in range(5):
        await remember(store, TENANT, Memory.from_write(STATEMENT))
    after = await store.get_object(TENANT, first.object_id)

    assert before is not None and after is not None
    assert after.confidence == before.confidence


# -- what must NOT be folded --------------------------------------------------
async def test_a_correction_is_never_folded_into_what_it_corrects(store):
    """A correction is *supposed* to resemble what it corrects. Deduplicating it
    would discard the correction and leave the stale fact standing — the worst
    possible outcome for a memory system."""
    original = await remember(store, TENANT, Memory.from_write("Chris works at Acme Corp."))
    correction = await remember(
        store,
        TENANT,
        Memory.from_write(
            "Chris works at Acme Corporation now, as a staff engineer.",
            kind=MemoryKind.CORRECTION,
            supersedes=original.object_id,
        ),
    )

    assert correction.created, "the correction must not be folded into the stale fact"
    active = await store.list_objects(TENANT, type=None, limit=100)
    assert {o.id for o in active} == {correction.object_id}


async def test_distinct_facts_are_not_folded_together(store):
    for content in (
        "I prefer fixed-point integers for money.",
        "I never use Docker for local development.",
        "My name is Chris.",
    ):
        assert (await remember(store, TENANT, Memory.from_write(content))).created
    assert len(await store.list_objects(TENANT, limit=100)) == 3


async def test_the_same_words_in_a_different_scope_are_a_different_fact(store):
    """Two projects may legitimately hold the same sentence about themselves."""
    ledger = Scope(type=ScopeType.PROJECT, id="proj_ledger")
    atlas = Scope(type=ScopeType.PROJECT, id="proj_atlas")

    a = await remember(store, TENANT, Memory.from_write("This ships in March.", scope=ledger))
    b = await remember(store, TENANT, Memory.from_write("This ships in March.", scope=atlas))

    assert a.created and b.created
    assert len(await store.list_objects(TENANT, limit=100)) == 2


async def test_dedup_can_be_declined_for_exact_writes(store):
    """A replay or a migration must be able to write what it was given."""
    await remember(store, TENANT, Memory.from_write(STATEMENT))
    second = await remember(store, TENANT, Memory.from_write(STATEMENT), dedup=False)

    assert second.created
    assert len(await store.list_objects(TENANT, limit=100)) == 2


async def test_a_supplied_event_survives_ingestion(store):
    """The connector tags its writes; ingestion must not swallow that."""
    memory = Memory.from_write(STATEMENT)
    await remember(
        store,
        TENANT,
        memory,
        event=Event(type=EventType.CONNECTOR_WRITE, object_id=memory.id, actor=Actor.CONNECTOR),
    )
    events = await store.list_events(TENANT, object_id=memory.id)
    assert events[0].type is EventType.CONNECTOR_WRITE


async def test_lookup_finds_the_duplicate_it_will_fold_into(store):
    stored = await remember(store, TENANT, Memory.from_write(STATEMENT))
    found = await find_duplicate(store, TENANT, RESTATED)
    assert found is not None and found.id == stored.object_id
    assert await find_duplicate(store, TENANT, "Something entirely unrelated.") is None


# -- and retrieval is unaffected ----------------------------------------------
async def test_retrieval_still_returns_the_fact_once(store):
    for _ in range(4):
        await remember(store, TENANT, Memory.from_write(STATEMENT))

    context = await retrieve(store, TENANT, "how should I represent money", trace=False)

    assert [o.content for o in context.objects] == [STATEMENT]
    # Nothing to deduplicate at read time, because nothing duplicated at write time.
    assert context.deduplicated == 0
