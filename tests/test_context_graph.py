"""The Atlas graph endpoint: entities as hubs, facts as what mentions them.

The library view listed 3,818 objects flat, 86% of which were entities and facts
rather than memories about the user — identity lines like "A university." rendered
as peers of the things they describe. The structure to fix that already existed as
`mentions` edges and was simply never read.
"""

from __future__ import annotations

import pytest

from coletar.schema.objects import (
    ContextObject,
    Edge,
    EdgeType,
    ExtractionMethod,
    Memory,
    ObjectType,
    OriginType,
    Provenance,
    Provider,
)
from coletar.schema.tenancy import tenant_id

TENANT = tenant_id("tenant_graph_test")


@pytest.fixture
def live_store(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Point the app's `build_store()` at a scratch snapshot.

    The Inspector reads the live store rather than an upload, so a test of the page
    has to stand one up — which is also the point: the old snapshot viewer could not
    know which tenant it was rendering, and this one cannot help but know.
    """
    from coletar.config import get_settings
    from coletar.store import reset_store

    monkeypatch.setenv("COLETAR_STORE_PATH", str(tmp_path / "store.json"))
    monkeypatch.setenv("COLETAR_STORE_BACKEND", "memory")
    monkeypatch.setenv("COLETAR_DEFAULT_TENANT_ID", str(TENANT))
    # Both are process-wide: settings are cached and the store is a singleton, so
    # without dropping each the app would answer from whatever an earlier test built.
    get_settings.cache_clear()
    reset_store()
    yield
    get_settings.cache_clear()
    reset_store()


def _provenance() -> Provenance:
    return Provenance(origin_type=OriginType.USER, provider=Provider.CHATGPT)


def _entity(name: str, description: str) -> ContextObject:
    return ContextObject(
        type=ObjectType.ENTITY,
        content=description,
        payload={"name": name},
        extraction_method=ExtractionMethod.MODEL_EXTRACTED,
        provenance=_provenance(),
    )


def _fact(content: str) -> ContextObject:
    return ContextObject(
        type=ObjectType.FACT,
        content=content,
        extraction_method=ExtractionMethod.MODEL_EXTRACTED,
        provenance=_provenance(),
    )


async def _seed(store) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Two hubs, one shared fact, and an entity nothing mentions."""
    bank = _entity("JP Morgan", "An investment bank.")
    school = _entity("Georgetown University", "A university.")
    island = _entity("Nobody", "Mentioned by nothing.")
    for obj in (bank, school, island):
        await store.put_object(TENANT, obj)

    shared = _fact("I studied then joined them.")
    only_bank = _fact("I work there now.")
    for obj in (shared, only_bank):
        await store.put_object(TENANT, obj)
    await store.put_object(TENANT, Memory.from_write("I prefer fixed-point money."))

    for src, dst in (
        (shared.id, bank.id),
        (shared.id, school.id),
        (only_bank.id, bank.id),
    ):
        await store.add_edge(TENANT, Edge(src_id=src, dst_id=dst, type=EdgeType.MENTIONS))
    return {"bank": bank.id, "school": school.id, "island": island.id, "shared": shared.id}


@pytest.mark.asyncio
async def test_the_overview_draws_entities_joined_by_what_mentions_them_both(
    live_store: None,
) -> None:
    """Entities do not link to each other; facts link to entities.

    So the overview's edges are co-mentions — two entities joined because one fact
    names both. Without that the constellation is a scatter of circles, which is a
    picture of nothing.
    """
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app
    from coletar.store import build_store

    ids = await _seed(build_store())
    graph = TestClient(app).get("/web-api/graph").json()

    assert {n["type"] for n in graph["nodes"]} == {"entity"}, (
        "the overview is an index of entities; facts belong to the drill-down"
    )
    labels = {n["label"] for n in graph["nodes"]}
    # The name, not the description: a node reading "An investment bank." is not
    # one anybody can find JP Morgan in.
    assert "JP Morgan" in labels and "Georgetown University" in labels

    pairs = {frozenset((e["src"], e["dst"])) for e in graph["edges"]}
    assert frozenset((ids["bank"], ids["school"])) in pairs

    # An entity nothing mentions is an island, and drawing hundreds of them is what
    # makes a graph view look like static.
    assert ids["island"] not in {n["id"] for n in graph["nodes"]}


@pytest.mark.asyncio
async def test_focusing_an_entity_returns_it_and_everything_that_mentions_it(
    live_store: None,
) -> None:
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app
    from coletar.store import build_store

    ids = await _seed(build_store())
    client = TestClient(app)
    graph = client.get("/web-api/graph", params={"focus": ids["bank"]}).json()

    by_type = {n["id"]: n["type"] for n in graph["nodes"]}
    assert by_type[ids["bank"]] == "entity"
    assert sum(1 for t in by_type.values() if t == "fact") == 2
    # Every fact is joined to the hub, which is what makes it a hub.
    assert all(e["dst"] == ids["bank"] for e in graph["edges"])
    assert client.get("/web-api/graph", params={"focus": "ent_missing"}).status_code == 404


@pytest.mark.asyncio
async def test_the_degree_is_reported_so_the_drawing_can_rank_by_it(
    live_store: None,
) -> None:
    """A hub with nine facts is the thing worth drawing; a name mentioned once is
    the long tail. The view sizes and orders by this rather than guessing."""
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app
    from coletar.store import build_store

    ids = await _seed(build_store())
    graph = TestClient(app).get("/web-api/graph").json()
    degree = {n["id"]: n["degree"] for n in graph["nodes"]}
    assert degree[ids["bank"]] == 2
    assert degree[ids["school"]] == 1
    # Reported so the view can say what it is not showing, rather than implying
    # the graph is smaller than it is.
    assert graph["total_entities"] == 3


@pytest.mark.asyncio
async def test_the_graph_is_far_smaller_than_the_workspace_snapshot(
    live_store: None,
) -> None:
    """The reason this is its own endpoint.

    `/web-api/state` ships every object to the browser — 6.6 MB on the real graph.
    Drawing needs the opposite shape: labels and edges, no payloads, no provenance.
    Serving both from one endpoint would send the same corpus twice.
    """
    import json

    from fastapi.testclient import TestClient

    from coletar.inspector.app import app
    from coletar.store import build_store

    await _seed(build_store())
    client = TestClient(app)
    graph = client.get("/web-api/graph").json()
    snapshot = client.get("/web-api/state").json()

    assert len(json.dumps(graph)) < len(json.dumps(snapshot))
    assert "provenance" not in json.dumps(graph)
