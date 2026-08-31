"""M4.4 — observability and the agentic view.

§6 is explicit that both are **views over the substrate**, not subsystems. The tests
that matter here are the ones that would fail if either started keeping its own
state: every number is derived from objects and events that already exist, and
producing a reading writes nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from coletar.inspector.metrics import (
    AGENTIC_TYPES,
    build_agentic_view,
    build_dashboard,
)
from coletar.retrieval import retrieve
from coletar.schema.events import EventType
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


def obj(content: str, object_type: ObjectType, *, sources: list[str] | None = None):
    return ContextObject(
        type=object_type,
        content=content,
        extraction_method=ExtractionMethod.DERIVED_SUMMARY,
        provenance=Provenance(
            origin_type=OriginType.AGENT,
            provider=Provider.COLETAR,
            source_object_ids=sources or [],
        ),
    )


async def seeded() -> InMemoryStore:
    store = InMemoryStore()
    await store.put_object(TENANT, Memory.from_write("Chris prefers tabs over spaces."))
    await store.put_object(
        TENANT, Memory.from_write("Chris ships C++20.", kind=MemoryKind.FACT)
    )
    return store


# --- it is a view, and stays one --------------------------------------------------


@pytest.mark.asyncio
async def test_building_a_dashboard_writes_nothing() -> None:
    """The property that keeps this a view. A dashboard that recorded its own reading
    would make the log describe the observer instead of the graph."""
    store = await seeded()
    before = len(await store.list_events(TENANT, limit=500))

    await build_dashboard(store, TENANT)
    await build_agentic_view(store, TENANT)

    assert len(await store.list_events(TENANT, limit=500)) == before


@pytest.mark.asyncio
async def test_last_access_is_derived_from_traces_not_an_access_event() -> None:
    """`OBJECT_ACCESSED` is declared and nothing emits it, which is the right
    outcome: an event per object per search would multiply the log by the width of
    every result set to record what the traces already imply."""
    store = await seeded()
    await retrieve(store, TENANT, "tabs", surface="mcp")

    events = await store.list_events(TENANT, limit=500)
    assert not any(e.type is EventType.OBJECT_ACCESSED for e in events)

    board = await build_dashboard(store, TENANT)
    read = [row for row in board.health if row.last_access is not None]
    assert read, "a search should have marked something as read"


# --- what the roadmap asked the dashboard to show ---------------------------------


@pytest.mark.asyncio
async def test_it_reports_size_counts_and_retirement() -> None:
    store = await seeded()
    stale = Memory.from_write("Chris works at Acme.")
    await store.put_object(TENANT, stale)
    await store.put_object(
        TENANT, Memory.from_write("Chris works at Globex.", supersedes=stale.id)
    )
    retired = Memory.from_write("An old note.")
    await store.put_object(TENANT, retired)
    await store.retire_object(TENANT, retired.id, reason="test")

    board = await build_dashboard(store, TENANT)
    assert board.total_objects == 5
    assert board.retired == 1
    assert board.superseded == 1
    assert board.total_bytes > 0
    assert board.by_type["memory"] == 5


@pytest.mark.asyncio
async def test_it_groups_retrieval_by_surface() -> None:
    """§6 groups by surface because "the local bridge injected this" and "Claude
    asked" are different facts about the same graph."""
    store = await seeded()
    await retrieve(store, TENANT, "tabs", surface="mcp")
    await retrieve(store, TENANT, "tabs", surface="proxy")
    await retrieve(store, TENANT, "C++", surface="proxy")

    board = await build_dashboard(store, TENANT)
    usage = {u.surface: u for u in board.usage}
    assert usage["mcp"].searches == 1
    assert usage["proxy"].searches == 2
    assert usage["proxy"].p95_ms >= usage["proxy"].p50_ms


@pytest.mark.asyncio
async def test_it_can_explain_the_last_search() -> None:
    """"Why did I get this?" answerable from the page rather than from a log grep."""
    store = await seeded()
    await retrieve(store, TENANT, "tabs", surface="cli")

    board = await build_dashboard(store, TENANT)
    assert board.last_explanation
    assert "total" in board.last_explanation[0]
    assert "lexical" in board.last_explanation[0]


@pytest.mark.asyncio
async def test_never_read_and_expired_objects_are_surfaced() -> None:
    """The objects worth retiring are the ones costing tokens without being read."""
    store = await seeded()
    stale = Memory.from_write("A note nobody ever needed.")
    stale.ttl_days = 1
    stale.created_at = datetime.now(UTC) - timedelta(days=30)
    await store.put_object(TENANT, stale)

    board = await build_dashboard(store, TENANT)
    assert board.never_read == board.total_objects  # nothing searched yet
    assert board.expired == 1
    # Never-read and largest first, so the page opens on what to act on.
    assert board.health[0].never_read


# --- the agentic view is a filter, not a second store -----------------------------


@pytest.mark.asyncio
async def test_the_agentic_view_reads_the_same_graph() -> None:
    store = await seeded()
    await store.put_object(TENANT, obj("Acme Corp", ObjectType.ENTITY))
    await store.put_object(TENANT, obj("Chris left Acme in March.", ObjectType.FACT))

    view = await build_agentic_view(store, TENANT)
    assert view.total == 2
    assert [o.content for o in view.by_type["entity"]] == ["Acme Corp"]
    # The memories are in the same store and simply not part of this rendering.
    assert len(await store.list_objects(TENANT, limit=50)) == 4


@pytest.mark.asyncio
async def test_episode_lineage_survives() -> None:
    """§6 requires episode-to-derived-object lineage to be preserved. Without it the
    view is pretty and unfalsifiable — you cannot check what an episode produced."""
    store = await seeded()
    episode = obj("A long planning session.", ObjectType.EPISODE)
    await store.put_object(TENANT, episode)
    derived = obj("The team chose Postgres.", ObjectType.FACT, sources=[episode.id])
    await store.put_object(TENANT, derived)

    view = await build_agentic_view(store, TENANT)
    assert [o.id for o in view.derived_from[episode.id]] == [derived.id]


def test_the_agentic_view_covers_exactly_the_three_types() -> None:
    assert set(AGENTIC_TYPES) == {ObjectType.ENTITY, ObjectType.FACT, ObjectType.EPISODE}
