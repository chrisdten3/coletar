"""Object detail: lineage above, reach below.

Two things are worth testing hard here, and neither is the markup.

The first is that editing reach actually changes what a surface receives. A toggle
that renders correctly and does not move the store is worse than no toggle — it is
a demo that lies, and the whole point of this screen is that locality stops being a
claim and becomes a control.

The second is that granting every surface produces `SYNCED` rather than
`LOCAL_ONLY` naming all three. Those are indistinguishable to a reader today and
diverge the moment a fourth surface exists: the first means "wherever I go", the
second freezes a list that was accurate when it was written.
"""

from __future__ import annotations

import pytest

from coletar.inspector.detail import render_detail, surfaces_from_form
from coletar.inspector.library import SWITCHABLE, render_library
from coletar.inspector.review import InspectorError, set_locality
from coletar.schema.events import EventType
from coletar.schema.objects import (
    Locality,
    LocalityMode,
    Memory,
    Provider,
)
from coletar.store.memory import InMemoryStore
from conftest import TENANT

CLAUDE_ONLY = Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset({Provider.CLAUDE}))
PRIVILEGED = "Handling the Northwind litigation matter."


@pytest.fixture
async def store() -> InMemoryStore:
    return InMemoryStore()


async def _restricted(store: InMemoryStore) -> Memory:
    memory = Memory.from_write(PRIVILEGED, locality=CLAUDE_ONLY)
    await store.put_object(TENANT, memory)
    return memory


# -- rendering ----------------------------------------------------------------
async def test_the_page_shows_both_halves(store: InMemoryStore):
    obj = await _restricted(store)
    html = await render_detail(store, TENANT, obj.id)

    assert "Lineage · read-only" in html
    assert "Reach · editable" in html
    assert PRIVILEGED in html


async def test_reach_states_which_surfaces_can_read_it(store: InMemoryStore):
    obj = await _restricted(store)
    html = await render_detail(store, TENANT, obj.id)

    assert "1 of 3 surfaces can read this" in html
    assert "withheld from ChatGPT, the local model" in html


async def test_every_switchable_surface_gets_a_control(store: InMemoryStore):
    obj = await _restricted(store)
    html = await render_detail(store, TENANT, obj.id)

    for surface in SWITCHABLE:
        assert f'value="{surface}"' in html


async def test_the_owner_can_open_an_object_they_restricted(store: InMemoryStore):
    """Loaded with no caller_surface on purpose. An owner locked out of the object
    they restricted could never lift the restriction — a one-way door."""
    obj = await _restricted(store)
    assert PRIVILEGED in await render_detail(store, TENANT, obj.id)


async def test_lineage_reads_oldest_first(store: InMemoryStore):
    obj = await _restricted(store)
    await set_locality(store, TENANT, obj.id, surfaces=frozenset(SWITCHABLE))
    html = await render_detail(store, TENANT, obj.id)

    assert html.index("Created") < html.index("Reach changed")


async def test_lineage_names_the_source_episode(store: InMemoryStore):
    memory = Memory.from_write("From a captured turn.", source_object_ids=["ep_18f2c7"])
    await store.put_object(TENANT, memory)
    html = await render_detail(store, TENANT, memory.id)

    assert "Derived from a captured turn" in html
    assert "ep_18f2c7" in html


async def test_a_locality_change_is_readable_in_the_timeline(store: InMemoryStore):
    obj = await _restricted(store)
    await set_locality(store, TENANT, obj.id, surfaces=frozenset({Provider.CHATGPT}))
    html = await render_detail(store, TENANT, obj.id)

    assert "Reach changed" in html
    assert "local_only:claude → local_only:chatgpt" in html


async def test_an_unknown_object_is_refused(store: InMemoryStore):
    with pytest.raises(InspectorError, match="no object"):
        await render_detail(store, TENANT, "mem_nope")


async def test_content_is_escaped(store: InMemoryStore):
    memory = Memory.from_write("<script>alert(1)</script>")
    await store.put_object(TENANT, memory)
    html = await render_detail(store, TENANT, memory.id)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# -- editing reach ------------------------------------------------------------
async def test_granting_a_surface_changes_what_that_surface_receives(store: InMemoryStore):
    """The test that would catch a toggle which renders but does not move."""
    obj = await _restricted(store)
    assert PRIVILEGED not in await render_library(store, TENANT, surface=Provider.CHATGPT)

    await set_locality(
        store, TENANT, obj.id, surfaces=frozenset({Provider.CLAUDE, Provider.CHATGPT})
    )

    assert PRIVILEGED in await render_library(store, TENANT, surface=Provider.CHATGPT)
    assert PRIVILEGED not in await render_library(store, TENANT, surface=Provider.LOCAL)


async def test_granting_every_surface_means_synced_not_a_frozen_list(store: InMemoryStore):
    obj = await _restricted(store)
    updated = await set_locality(store, TENANT, obj.id, surfaces=frozenset(SWITCHABLE))

    assert updated.locality.mode is LocalityMode.SYNCED
    assert not updated.locality.surfaces


async def test_restricting_a_shared_object_withholds_it(store: InMemoryStore):
    memory = Memory.from_write("Was visible everywhere.")
    await store.put_object(TENANT, memory)

    await set_locality(store, TENANT, memory.id, surfaces=frozenset({Provider.CLAUDE}))

    assert "Was visible everywhere." not in await render_library(
        store, TENANT, surface=Provider.CHATGPT
    )


async def test_restricting_to_nobody_is_refused(store: InMemoryStore):
    """`Locality` already rejects this; the message here is about what the user
    tried to do rather than about the model."""
    obj = await _restricted(store)
    with pytest.raises(InspectorError, match="retire it instead"):
        await set_locality(store, TENANT, obj.id, surfaces=frozenset())


async def test_a_locality_change_appends_exactly_one_event(store: InMemoryStore):
    obj = await _restricted(store)
    await set_locality(store, TENANT, obj.id, surfaces=frozenset({Provider.CHATGPT}))

    events = await store.list_events(TENANT, object_id=obj.id)
    changes = [e for e in events if e.type is EventType.OBJECT_LOCALITY_CHANGED]
    assert len(changes) == 1
    assert changes[0].detail["from"] == "local_only:claude"
    assert changes[0].detail["to"] == "local_only:chatgpt"


async def test_an_unchanged_selection_writes_nothing(store: InMemoryStore):
    """Re-submitting the form as it was displayed must not manufacture history."""
    obj = await _restricted(store)
    before = len(await store.list_events(TENANT, object_id=obj.id))

    await set_locality(store, TENANT, obj.id, surfaces=frozenset({Provider.CLAUDE}))

    assert len(await store.list_events(TENANT, object_id=obj.id)) == before


async def test_deciding_where_a_fact_may_go_counts_as_reviewing_it(store: InMemoryStore):
    from coletar.inspector.review import review_status

    obj = await _restricted(store)
    await set_locality(store, TENANT, obj.id, surfaces=frozenset({Provider.CHATGPT}))

    status = await review_status(store, TENANT)
    assert obj.id not in {o.id for o in status.unreviewed}


async def test_changing_a_missing_object_is_refused(store: InMemoryStore):
    with pytest.raises(InspectorError, match="no object"):
        await set_locality(store, TENANT, "mem_nope", surfaces=frozenset({Provider.CLAUDE}))


# -- form parsing -------------------------------------------------------------
def test_posted_surfaces_parse():
    assert surfaces_from_form(["claude", "chatgpt"]) == frozenset(
        {Provider.CLAUDE, Provider.CHATGPT}
    )


def test_an_unknown_posted_surface_is_refused_not_ignored():
    """Dropping it would silently apply a narrower policy than was selected."""
    with pytest.raises(InspectorError, match="unknown surface"):
        surfaces_from_form(["claude", "bogus"])


def test_a_surface_with_no_connector_cannot_be_granted():
    with pytest.raises(InspectorError, match="not a surface you can grant"):
        surfaces_from_form(["gemini"])


# -- the routes ---------------------------------------------------------------
@pytest.fixture
def live(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from coletar.config import get_settings
    from coletar.store import build_store, reset_store

    monkeypatch.setenv("COLETAR_STORE_PATH", str(tmp_path / "store.json"))
    monkeypatch.setenv("COLETAR_STORE_BACKEND", "memory")
    monkeypatch.setenv("COLETAR_DEFAULT_TENANT_ID", str(TENANT))
    get_settings.cache_clear()
    reset_store()
    yield build_store()
    get_settings.cache_clear()
    reset_store()


def _client():  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app

    return TestClient(app)


async def test_the_library_links_to_each_object(live):  # type: ignore[no-untyped-def]
    memory = Memory.from_write("Linked from the library.")
    await live.put_object(TENANT, memory)

    assert f'href="/object/{memory.id}"' in _client().get("/").text


async def test_posting_reach_updates_the_object(live):  # type: ignore[no-untyped-def]
    memory = Memory.from_write(PRIVILEGED, locality=CLAUDE_ONLY)
    await live.put_object(TENANT, memory)

    response = _client().post(
        "/locality",
        data={"object_id": memory.id, "surfaces": ["claude", "chatgpt"]},
        follow_redirects=True,
    )

    assert response.status_code == 200
    updated = await live.get_object(TENANT, memory.id)
    assert updated is not None
    assert updated.locality.visible_to(Provider.CHATGPT)


async def test_posting_no_surfaces_reports_the_refusal(live):  # type: ignore[no-untyped-def]
    memory = Memory.from_write(PRIVILEGED, locality=CLAUDE_ONLY)
    await live.put_object(TENANT, memory)

    response = _client().post(
        "/locality", data={"object_id": memory.id}, follow_redirects=True
    )

    assert "retire it instead" in response.text
    unchanged = await live.get_object(TENANT, memory.id)
    assert unchanged is not None
    assert unchanged.locality == CLAUDE_ONLY


def test_an_unknown_object_renders_a_404(live):  # type: ignore[no-untyped-def]
    response = _client().get("/object/mem_nope")

    assert response.status_code == 404
    assert "not in this tenant" in response.text
