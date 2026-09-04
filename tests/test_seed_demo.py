"""The demo graph.

A seed that only proved objects exist would be a fixture. What makes this one worth
testing is that the *history* is real: every past event came from calling the
operation that produces it, so a change which breaks lineage breaks the demo here
rather than in the room. The assertions below are about that history, not about the
six sentences.
"""

from __future__ import annotations

import pytest

from coletar.capture import is_pending
from coletar.inspector.detail import render_detail
from coletar.inspector.library import render_library
from coletar.schema.events import EventType
from coletar.schema.objects import LocalityMode, ObjectType, Provider
from coletar.seed_demo import seed_demo
from coletar.store.memory import InMemoryStore
from conftest import TENANT


@pytest.fixture
async def seeded() -> InMemoryStore:
    store = InMemoryStore()
    await seed_demo(store, TENANT)
    return store


async def test_the_whiteboard_scenario_holds(seeded: InMemoryStore):
    """The demo's one claim, asserted as counts: three surfaces, three graphs."""
    owner = await render_library(seeded, TENANT, surface=None)
    claude = await render_library(seeded, TENANT, surface=Provider.CLAUDE)
    chatgpt = await render_library(seeded, TENANT, surface=Provider.CHATGPT)
    local = await render_library(seeded, TENANT, surface=Provider.LOCAL)

    assert "6 objects" in owner
    assert "5 objects" in claude and "1 withheld" in claude
    assert "4 objects" in chatgpt and "2 withheld" in chatgpt
    assert "5 objects" in local and "1 withheld" in local


async def test_memories_come_from_more_than_one_provider(seeded: InMemoryStore):
    """A demo where every memory arrived the same way cannot show portability."""
    objects = await seeded.list_objects(TENANT, limit=100)
    providers = {obj.provenance.provider for obj in objects}

    assert {Provider.CLAUDE, Provider.CHATGPT, Provider.LOCAL} <= providers


async def test_export_derived_memories_are_less_confident_than_live_writes(
    seeded: InMemoryStore,
):
    """Confidence is never passed by the seed; it defaults from extraction_method,
    so this is the §3.1 distinction rather than a number someone picked."""
    objects = await seeded.list_objects(TENANT, limit=100)
    by_method = {obj.extraction_method: obj.confidence for obj in objects}

    assert by_method["account_export_parse"] < by_method["mcp_live_write"]


async def test_a_correction_left_a_supersedes_chain(seeded: InMemoryStore):
    objects = await seeded.list_objects(TENANT, limit=100)
    corrected = next(o for o in objects if "fixed-point" in o.content)

    assert corrected.supersedes is not None
    superseded = await seeded.get_object(TENANT, corrected.supersedes)
    assert superseded is not None and "floats" in superseded.content
    assert superseded.id not in {o.id for o in objects}, "a stale fact is still served"


async def test_the_edited_memory_kept_what_it_used_to_say(seeded: InMemoryStore):
    """Constraint 6: the event carries before/after, so nothing was lost in place."""
    objects = await seeded.list_objects(TENANT, limit=100)
    deploy = next(o for o in objects if "Fly.io" in o.content)

    events = await seeded.list_events(TENANT, object_id=deploy.id)
    assert any(e.type is EventType.OBJECT_UPDATED for e in events)
    assert any("Heroku" in str(e.before) for e in events if e.before is not None)


async def test_the_restricted_memory_was_restricted_after_the_fact(seeded: InMemoryStore):
    """The demo's story: written like anything else, then withheld. The moment the
    user decided has to be findable, which is what the event type is for."""
    objects = await seeded.list_objects(TENANT, limit=100)
    northwind = next(o for o in objects if "Northwind" in o.content)

    events = await seeded.list_events(TENANT, object_id=northwind.id)
    changed = [e for e in events if e.type is EventType.OBJECT_LOCALITY_CHANGED]
    assert len(changed) == 1
    assert changed[0].detail["from"] == "synced"
    assert changed[0].detail["to"] == "local_only:claude"


async def test_both_routes_to_a_withheld_object_are_present(seeded: InMemoryStore):
    """One restricted at write time, one restricted later — the two ways a user
    actually arrives at a withheld memory."""
    memories = await seeded.list_objects(TENANT, type=ObjectType.MEMORY, limit=100)
    restricted = [o for o in memories if o.locality.mode is LocalityMode.LOCAL_ONLY]
    assert len(restricted) == 2

    with_change = 0
    for obj in restricted:
        events = await seeded.list_events(TENANT, object_id=obj.id)
        if any(e.type is EventType.OBJECT_LOCALITY_CHANGED for e in events):
            with_change += 1
    assert with_change == 1


async def test_captured_turns_are_real_objects_the_lineage_can_point_at(
    seeded: InMemoryStore,
):
    """The reason the history is made rather than written: a source episode that
    did not exist would 404 the first time anyone clicked it on stage."""
    objects = await seeded.list_objects(TENANT, limit=100)
    derived = [o for o in objects if o.provenance.source_object_ids]
    assert derived, "no memory claims a source"

    for memory in derived:
        for source_id in memory.provenance.source_object_ids:
            episode = await seeded.get_object(TENANT, source_id)
            assert episode is not None, f"{memory.id} points at a missing {source_id}"
            assert episode.type is ObjectType.EPISODE
            # A turn that yielded this memory has by definition been judged; the
            # pending ones are the turns nothing derives from.
            assert not is_pending(episode)


async def test_every_seeded_memory_has_a_lineage_worth_reading(seeded: InMemoryStore):
    """The gap this seed closed: a one-node timeline looks broken in a demo.

    Memories only. An episode is one event by definition — it was captured, and
    nothing has happened to it yet — and demanding a history it cannot have would
    be a test asserting the wrong thing about the right object.
    """
    memories = await seeded.list_objects(TENANT, type=ObjectType.MEMORY, limit=100)
    thin = []
    for obj in memories:
        html = await render_detail(seeded, TENANT, obj.id)
        if html.count("node-title") < 2:
            thin.append(obj.content[:40])

    assert not thin, f"objects with a single-node lineage: {thin}"


async def test_no_captured_turn_claims_to_be_both_pending_and_extracted(
    seeded: InMemoryStore,
):
    """A state the real pipeline cannot reach. `extract_pending` clears the flag
    when it writes objects, so an episode that produced memories while still
    flagged pending is data that could not exist — and a demo whose data is
    impossible invites the one question you cannot answer."""
    episodes = await seeded.list_objects(TENANT, type=ObjectType.EPISODE, limit=100)
    everything = await seeded.list_objects(TENANT, limit=200)

    produced: dict[str, int] = {}
    for obj in everything:
        for source in obj.provenance.source_object_ids:
            produced[source] = produced.get(source, 0) + 1

    for episode in episodes:
        if produced.get(episode.id):
            assert not is_pending(episode), f"{episode.id} yielded memories while pending"


async def test_the_queue_shows_both_a_pending_and_a_finished_turn(seeded: InMemoryStore):
    """One of each, so the capture page demonstrates the states rather than one."""
    episodes = await seeded.list_objects(TENANT, type=ObjectType.EPISODE, limit=100)
    pending = [e for e in episodes if is_pending(e)]

    assert pending, "nothing is queued; the health strip has nothing to count"
    assert len(pending) < len(episodes), "nothing has been extracted yet"
