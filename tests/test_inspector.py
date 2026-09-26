"""M5.3 — Inspector operations and the compile gate.

The requirement this file defends: nothing compiles until a human has seen what it
says. The subtle half is that a review is a statement about an object *as it was
then*, so the interesting tests are the ones where an approval has to stop counting.
"""

from __future__ import annotations

import re

import pytest

from coletar.inspector.review import (
    InspectorError,
    edit,
    erase_episode,
    mark_reviewed,
    merge,
    rescope,
    review_status,
)
from coletar.schema.events import EventType
from coletar.schema.objects import GLOBAL_SCOPE, Memory, MemoryKind, Scope, ScopeType
from coletar.store.memory import InMemoryStore
from conftest import TENANT

PROJECT = Scope(type=ScopeType.PROJECT, id="proj_ledger")


async def seeded(*contents: str) -> tuple[InMemoryStore, list[Memory]]:
    store = InMemoryStore()
    objects = []
    for content in contents:
        obj = Memory.from_write(content, kind=MemoryKind.PREFERENCE)
        await store.put_object(TENANT, obj)
        objects.append(obj)
    return store, objects


# --- the gate -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_is_closed_until_every_eligible_object_is_seen() -> None:
    store, (a, b) = await seeded("Chris prefers tabs.", "Chris works late.")

    status = await review_status(store, TENANT)
    assert not status.can_compile
    assert len(status.unreviewed) == 2

    await mark_reviewed(store, TENANT, a.id)
    assert not (await review_status(store, TENANT)).can_compile

    await mark_reviewed(store, TENANT, b.id)
    status = await review_status(store, TENANT)
    assert status.can_compile
    assert status.reviewed_count == 2


@pytest.mark.asyncio
async def test_review_does_not_survive_a_change_to_what_was_reviewed() -> None:
    """The half of the requirement that is easy to miss.

    A review says "I have seen what this says". Once the object says something else,
    that statement is about text nobody approved — so a stale approval must stop
    opening the gate, or the gate certifies content no human ever read.
    """
    store, (obj,) = await seeded("Chris works at Acme.")
    await mark_reviewed(store, TENANT, obj.id)
    assert (await review_status(store, TENANT)).can_compile

    stored = await store.get_object(TENANT, obj.id)
    assert stored is not None
    stored.content = "Chris works at Globex."
    await store.put_object(TENANT, stored)

    status = await review_status(store, TENANT)
    assert not status.can_compile
    assert [o.id for o in status.unreviewed] == [obj.id]


@pytest.mark.asyncio
async def test_erasing_a_raw_episode_retires_it_and_shreds_its_key() -> None:
    from coletar.capture import capture_turn
    from coletar.episode_crypto import EpisodeKeyUnavailable, decrypt_episode
    from coletar.schema.objects import Provider

    store = InMemoryStore()
    episode = await capture_turn(store, TENANT, "private turn", surface=Provider.CHATGPT)

    await erase_episode(store, TENANT, episode.id)

    assert await store.list_objects(TENANT, type=episode.type) == []
    retained = await store.get_object(TENANT, episode.id)
    assert retained is not None and retained.retired_at is not None
    with pytest.raises(EpisodeKeyUnavailable):
        await decrypt_episode(store, TENANT, retained)
    events = await store.list_events(TENANT, object_id=episode.id)
    assert any(event.type is EventType.OBJECT_SHREDDED for event in events)


@pytest.mark.asyncio
async def test_gate_watches_exactly_the_set_the_compiler_would_move() -> None:
    """The gate imports the compiler's own eligibility rule. If it reimplemented it,
    the two could drift and the gate would be guarding a different population than
    the one that leaves."""
    store, (stale,) = await seeded("Chris works at Acme.")
    correction = Memory.from_write("Chris works at Globex.", supersedes=stale.id)
    await store.put_object(TENANT, correction)

    status = await review_status(store, TENANT)
    assert [o.id for o in status.eligible] == [correction.id]

    await mark_reviewed(store, TENANT, correction.id)
    assert (await review_status(store, TENANT)).can_compile


@pytest.mark.asyncio
async def test_review_state_is_derived_from_the_log_not_stored_on_the_object() -> None:
    """§2: a property that applies to one workflow does not earn a column, and the
    log is already the provenance record. A boolean would be a second source of
    truth that replay could not reconstruct."""
    store, (obj,) = await seeded("Chris prefers tabs.")
    await mark_reviewed(store, TENANT, obj.id)

    stored = await store.get_object(TENANT, obj.id)
    assert stored is not None
    assert not hasattr(stored, "reviewed")
    assert "reviewed" not in stored.payload

    events = await store.list_events(TENANT, object_id=obj.id)
    assert any(e.type is EventType.OBJECT_REVIEWED for e in events)


# --- edit ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_corrects_the_record_without_inventing_a_history() -> None:
    """A supersedes chain means "this used to be true and now something else is",
    which is a claim about the world. A bad extraction is a claim about the record,
    and filing it as a supersession would make the chain useless for what it exists
    to express."""
    store, (obj,) = await seeded("Chris prefers tabs")
    await edit(store, TENANT, obj.id, content="Chris prefers spaces")

    stored = await store.get_object(TENANT, obj.id)
    assert stored is not None
    assert stored.content == "Chris prefers spaces"
    assert stored.supersedes is None
    assert stored.version == 2


@pytest.mark.asyncio
async def test_edit_keeps_what_the_object_used_to_say(tmp_path: object) -> None:
    """Constraint 6. Nothing is lost by editing in place, because the event carries
    full before/after state."""
    store, (obj,) = await seeded("Chris works at Acme.")
    await edit(store, TENANT, obj.id, content="Chris works at Globex.")

    events = await store.list_events(TENANT, object_id=obj.id)
    revision = next(e for e in events if e.before and e.before.get("content"))
    assert revision.before is not None and revision.after is not None
    assert revision.before["content"] == "Chris works at Acme."
    assert revision.after["content"] == "Chris works at Globex."


@pytest.mark.asyncio
async def test_editing_counts_as_reviewing() -> None:
    """The user just read it closely enough to change it."""
    store, (obj,) = await seeded("Chris prefers tabs")
    await edit(store, TENANT, obj.id, content="Chris prefers spaces")
    assert (await review_status(store, TENANT)).can_compile


@pytest.mark.asyncio
async def test_edit_refuses_to_empty_an_object() -> None:
    store, (obj,) = await seeded("Chris prefers tabs")
    with pytest.raises(InspectorError, match="retire"):
        await edit(store, TENANT, obj.id, content="   ")


# --- re-scope -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_rescope_is_where_a_misfiled_fact_gets_caught() -> None:
    """`scope_preservation` is a hard gate on the compiler, but the compiler can
    only preserve the scope it is given. A project fact filed globally would be
    compiled faithfully into every destination the user owns."""
    store, (obj,) = await seeded("The ledger project settled on double-entry.")
    assert obj.scope == GLOBAL_SCOPE

    await rescope(store, TENANT, obj.id, scope=PROJECT)
    stored = await store.get_object(TENANT, obj.id)
    assert stored is not None and stored.scope == PROJECT

    events = await store.list_events(TENANT, object_id=obj.id)
    rescoped = next(e for e in events if e.type is EventType.OBJECT_RESCOPED)
    assert rescoped.detail == {"from": "global", "to": "project:proj_ledger"}


@pytest.mark.asyncio
async def test_rescoping_counts_as_reviewing() -> None:
    store, (obj,) = await seeded("Ledger uses double-entry.")
    await rescope(store, TENANT, obj.id, scope=PROJECT)
    assert (await review_status(store, TENANT)).can_compile


# --- merge --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_folds_a_duplicate_away_without_deleting_it() -> None:
    store, (survivor, absorbed) = await seeded(
        "Chris prefers fixed-point integers for money.",
        "Chris likes fixed-point integers for money.",
    )
    await merge(store, TENANT, survivor_id=survivor.id, absorbed_id=absorbed.id)

    status = await review_status(store, TENANT)
    assert [o.id for o in status.eligible] == [survivor.id]
    # Constraint 6: retired from the working set, still readable for provenance.
    assert await store.get_object(TENANT, absorbed.id) is not None


@pytest.mark.asyncio
async def test_merge_refuses_to_clobber_an_existing_chain() -> None:
    """Chaining silently would drop the earlier link, and with it the older
    object's route back into history."""
    store, (a, b, c) = await seeded("one", "two", "three")
    await merge(store, TENANT, survivor_id=a.id, absorbed_id=b.id)
    with pytest.raises(InspectorError, match="already supersedes"):
        await merge(store, TENANT, survivor_id=a.id, absorbed_id=c.id)


@pytest.mark.asyncio
async def test_merge_refuses_self_and_unknown_objects() -> None:
    store, (obj,) = await seeded("one")
    with pytest.raises(InspectorError, match="into itself"):
        await merge(store, TENANT, survivor_id=obj.id, absorbed_id=obj.id)
    with pytest.raises(InspectorError, match="no object"):
        await merge(store, TENANT, survivor_id=obj.id, absorbed_id="mem_missing")


@pytest.mark.asyncio
async def test_operations_refuse_objects_from_another_tenant() -> None:
    """`get_object` returns None across tenants, so every operation refuses rather
    than reaching into a graph it was not asked about."""
    from coletar.schema.tenancy import tenant_id

    store, (obj,) = await seeded("Chris prefers tabs.")
    other = tenant_id("tenant_other")
    with pytest.raises(InspectorError, match="no object"):
        await edit(store, other, obj.id, content="anything")


# --- the page ------------------------------------------------------------------


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


def _get(path: str = "/") -> str:
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app

    response = TestClient(app, base_url="http://localhost").get(path)
    assert response.status_code == 200
    return response.text


@pytest.mark.asyncio
async def test_page_names_the_tenant_it_is_showing(live_store: None) -> None:
    """The snapshot viewer's one real defect, gone structurally rather than fixed:
    a page bound to the live store cannot render a graph without knowing whose."""
    from coletar.store import build_store

    await build_store().put_object(TENANT, Memory.from_write("Chris prefers tabs."))
    assert str(TENANT) in _get()


@pytest.mark.asyncio
async def test_page_states_the_gate_rather_than_just_disabling_a_button(
    live_store: None,
) -> None:
    from coletar.store import build_store

    store = build_store()
    obj = Memory.from_write("Chris prefers tabs.")
    await store.put_object(TENANT, obj)

    blocked = _get()
    assert "Compile is blocked" in blocked
    assert "1 of 1 eligible objects have not been reviewed" in blocked

    await mark_reviewed(store, TENANT, obj.id)
    assert "Compile is available" in _get()


@pytest.mark.asyncio
async def test_page_escapes_object_content(live_store: None) -> None:
    """Content is model-written and, transitively, written by whatever those models
    read (§11). It renders as text or it is a stored XSS."""
    from coletar.store import build_store

    await build_store().put_object(TENANT, Memory.from_write('<script>alert("x")</script>'))
    body = _get()
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body


@pytest.mark.asyncio
async def test_page_shows_a_refusal_instead_of_swallowing_it(live_store: None) -> None:
    from coletar.store import build_store

    store = build_store()
    obj = Memory.from_write("Chris prefers tabs.")
    await store.put_object(TENANT, obj)

    from fastapi.testclient import TestClient

    from coletar.inspector.app import app

    client = TestClient(app, base_url="http://localhost")
    response = client.post(
        "/merge", data={"survivor_id": obj.id, "absorbed_id": obj.id}, follow_redirects=True
    )
    assert "cannot be merged into itself" in response.text


@pytest.mark.asyncio
async def test_page_shows_which_surfaces_may_receive_an_object(live_store: None) -> None:
    """The reviewer is the last check before a compile, so the card has to say where
    the object can end up — not only what it says."""
    from coletar.schema.objects import Locality, LocalityMode, Provider
    from coletar.store import build_store

    store = build_store()
    await store.put_object(TENANT, Memory.from_write("Chris prefers tabs."))
    await store.put_object(
        TENANT,
        Memory.from_write(
            "Private note.",
            locality=Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset({Provider.LOCAL})),
        ),
    )
    body = _get()
    assert "every surface" in body
    assert "local to local" in body


# --- the dashboard and agentic pages --------------------------------------------


@pytest.mark.asyncio
async def test_the_dashboard_page_renders_the_live_store(live_store: None) -> None:
    from coletar.retrieval import retrieve
    from coletar.store import build_store

    store = build_store()
    await store.put_object(TENANT, Memory.from_write("Chris prefers tabs."))
    await retrieve(store, TENANT, "tabs", surface="mcp")

    body = _get("/dashboard")
    assert "Retrieval by surface" in body
    assert "Why the last search returned what it did" in body
    assert "mcp" in body


@pytest.mark.asyncio
async def test_agentic_page_shows_and_can_erase_pending_raw_turn(live_store: None) -> None:
    from fastapi.testclient import TestClient

    from coletar.capture import capture_turn
    from coletar.inspector.app import app
    from coletar.schema.objects import ObjectType, Provider
    from coletar.store import build_store

    store = build_store()
    episode = await capture_turn(store, TENANT, "private turn", surface=Provider.CHATGPT)
    page = _get("/agentic")
    assert "Pending extraction: 1" in page
    assert "erase raw turn" in page

    response = TestClient(app, base_url="http://localhost").post(
        "/erase-episode", data={"object_id": episode.id}, follow_redirects=True
    )
    assert response.status_code == 200
    assert "Pending extraction: 0" in response.text
    assert await store.list_objects(TENANT, type=ObjectType.EPISODE) == []


@pytest.mark.asyncio
async def test_the_dashboard_escapes_object_ids_and_content(live_store: None) -> None:
    from coletar.store import build_store

    await build_store().put_object(TENANT, Memory.from_write('<script>alert("x")</script>'))
    body = _get("/agentic")
    assert "<script>alert" not in body


@pytest.mark.asyncio
async def test_the_agentic_page_says_it_is_a_filter_not_a_store(live_store: None) -> None:
    """The claim is load-bearing enough to be on the page: a second graph appearing
    behind the first is the failure §6 forbids."""
    body = _get("/agentic")
    assert "not a second store" in body
    assert "Episode lineage" in body


def test_every_page_offers_the_others(live_store: None) -> None:
    for path in ("/", "/dashboard", "/agentic"):
        body = _get(path)
        assert 'href="/dashboard"' in body
        assert 'href="/agentic"' in body


@pytest.mark.asyncio
async def test_library_filters_and_detail_are_tenant_scoped(live_store: None) -> None:
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app
    from coletar.schema.tenancy import tenant_id
    from coletar.store import build_store

    store = build_store()
    own = Memory.from_write("Use fixed-point arithmetic.", kind=MemoryKind.PREFERENCE)
    other = Memory.from_write("A different tenant's private fact.")
    await store.put_object(TENANT, own)
    await store.put_object(tenant_id("other-web-tenant"), other)
    client = TestClient(app, base_url="http://localhost")
    assert own.content in client.get("/?q=fixed&view=preference").text
    assert own.content not in client.get("/?q=missing").text
    assert "No matching context" in client.get("/?q=missing").text
    assert other.content not in client.get("/").text
    assert client.get(f"/objects/{other.id}").status_code == 404
    detail = client.get(f"/objects/{own.id}")
    assert detail.status_code == 200
    assert "object.created" in detail.text
    assert not (await review_status(store, TENANT)).can_compile
    client.post("/review", data={"object_id": own.id})
    assert own.content not in client.get("/?view=unreviewed").text
    assert "You’re all caught up" in client.get("/review").text


@pytest.mark.asyncio
async def test_web_detail_escapes_textarea_and_search_input(live_store: None) -> None:
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app
    from coletar.store import build_store

    obj = Memory.from_write('</textarea><script>alert("stored")</script>')
    await build_store().put_object(TENANT, obj)
    client = TestClient(app, base_url="http://localhost")
    detail = client.get(f"/objects/{obj.id}").text
    assert "<script>alert" not in detail
    assert "&lt;/textarea&gt;" in detail
    search = client.get("/", params={"q": '"><script>alert(1)</script>'}).text
    assert "<script>alert" not in search
    assert "&lt;script&gt;" in search


@pytest.mark.asyncio
async def test_product_review_compile_and_reach_flow(live_store: None) -> None:
    import io
    import zipfile

    from fastapi.testclient import TestClient

    from coletar.inspector.app import app
    from coletar.store import build_store

    client = TestClient(app, base_url="http://localhost")
    assert client.get("/app").status_code == 200
    created = client.post(
        "/web-api/memories",
        json={
            "content": "The private launch date is November 14.",
            "locality": {"mode": "local_only", "surfaces": ["claude"]},
        },
    )
    assert created.status_code == 200
    oid = created.json()["id"]
    assert (
        client.post("/web-api/compile/download", json={"destination": "chatgpt"}).status_code == 409
    )
    assert client.post(f"/web-api/objects/{oid}", json={"action": "review"}).status_code == 200
    preview = client.post("/web-api/compile/preview", json={"destination": "chatgpt"})
    assert preview.status_code == 200
    assert preview.json()["withheld"][0]["source_id"] == oid
    response = client.post("/web-api/compile/download", json={"destination": "chatgpt"})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        for name in z.namelist():
            if not name.endswith("manifest.json"):
                assert b"The private launch date" not in z.read(name)
    events = await build_store().list_events(TENANT)
    assert any(e.type is EventType.COMPILE_RUN for e in events)
    client.post(
        f"/web-api/objects/{oid}",
        json={
            "action": "reach",
            "locality": {"mode": "synced", "surfaces": []},
        },
    )
    assert not client.get("/web-api/state").json()["can_compile"]
    assert any(e.detail.get("field") == "locality" for e in await build_store().list_events(TENANT))


@pytest.mark.asyncio
async def test_product_import_recognises_both_providers_and_user_turns(live_store: None) -> None:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from coletar.inspector.app import app

    client = TestClient(app, base_url="http://localhost")
    for provider in ["claude", "chatgpt"]:
        path = (
            Path(__file__).parent / "fixtures" / "export_sources" / provider / "conversations.json"
        )
        response = client.post(
            "/web-api/import",
            files={"file": ("conversations.json", path.read_bytes(), "application/json")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["turns"] > 0
        assert response.json()["memories"] + response.json()["corroborated"] > 0
    state = client.get("/web-api/state").json()
    assert all(o["provenance"]["provider"] == "claude" for o in state["objects"])
    assert any(e["type"] == "object.corroborated" for e in state["events"])
    assert not state["can_compile"]
    malformed = client.post("/web-api/import", files={"file": ("bad.json", b"{broken")})
    assert malformed.status_code == 422
    unrelated = client.post("/web-api/import", files={"file": ("unrelated.json", b'[{"other":1}]')})
    assert unrelated.status_code == 422


@pytest.mark.asyncio
async def test_product_conflict_resolution_and_temporal_snapshot(live_store: None) -> None:
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app
    from coletar.store import build_store

    client = TestClient(app, base_url="http://localhost")
    assert client.post("/web-api/sample").status_code == 200
    assert client.post("/web-api/sample").status_code == 409
    response = client.post(
        "/web-api/resolve", json={"keep": "mem_ledger", "retire": "mem_ledger_conflict"}
    )
    assert response.status_code == 200
    retired = await build_store().get_object(TENANT, "mem_ledger_conflict")
    assert retired is not None and retired.retired_at is not None
    snapshot = client.get(
        "/web-api/audit", params={"at": "2026-03-03T23:59:59Z", "valid": "2026-01-01T12:00:00Z"}
    )
    assert snapshot.status_code == 200
    assert [o["id"] for o in snapshot.json()["objects"]] == ["mem_old_money"]
    assert snapshot.json()["signed"] is False


@pytest.mark.asyncio
async def test_product_refuses_cross_origin_mutations_and_other_tenant(live_store: None) -> None:
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app
    from coletar.schema.tenancy import tenant_id
    from coletar.store import build_store

    other = Memory.from_write("Other tenant secret")
    await build_store().put_object(tenant_id("other-web-tenant"), other)
    client = TestClient(app, base_url="http://localhost")
    assert other.id not in str(client.get("/web-api/state").json())
    assert client.post(f"/web-api/objects/{other.id}", json={"action": "review"}).status_code == 404
    assert (
        client.post(
            "/web-api/memories",
            headers={"Origin": "https://evil.example"},
            json={"content": "An unwanted memory"},
        ).status_code
        == 403
    )


@pytest.mark.asyncio
async def test_captured_source_is_not_a_compile_or_review_candidate(live_store: None) -> None:
    from coletar.capture import capture_turn
    from coletar.compiler.emit import compile_eligible
    from coletar.schema.objects import Provider
    from coletar.store import build_store

    store = build_store()
    episode = await capture_turn(store, TENANT, "Raw submitted source", surface=Provider.CLAUDE)
    assert compile_eligible([episode]) == []
    assert (await review_status(store, TENANT)).can_compile


@pytest.mark.asyncio
async def test_design_sample_populates_review_and_read_log(live_store: None) -> None:
    """The design fixture has to leave the dashboards with something to show.

    A seed that pre-reviews everything makes Review an empty screen, and a seed
    with no retrieval traces makes both Audit's read log and Settings' usage read
    as "nothing has ever happened" — which is exactly what the reference does not
    show. Assert the shape, not the wording.
    """
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app

    client = TestClient(app, base_url="http://localhost")
    assert client.post("/web-api/sample").status_code == 200
    state = client.get("/web-api/state").json()

    # The supersession and both sides of the conflict are what Review is about.
    unreviewed = set(state["unreviewed"])
    assert {"mem_money", "mem_ledger", "mem_ledger_conflict"} <= unreviewed
    assert "mem_northwind" not in unreviewed
    assert not state["can_compile"]

    # Usage is derived from real traces, so a populated Settings screen implies a
    # populated read log rather than a second fixture.
    assert set(state["usage"]) == {"claude", "claude_code", "local", "chatgpt"}
    assert state["usage"]["claude"] > state["usage"]["local"] > 0
    assert state["usage"]["chatgpt"] == 0

    traces = [e for e in state["events"] if e["type"] == "retrieval.trace"]
    assert len(traces) == 5
    withheld = [t for t in traces if not t["detail"]["returned_ids"]]
    assert len(withheld) == 1
    # `provider` is which assistant asked; `surface` is the door it came through.
    # The fixture records both the way a real read does.
    assert withheld[0]["detail"]["provider"] == "chatgpt"
    assert withheld[0]["detail"]["surface"] == "mcp"
    assert withheld[0]["detail"]["withheld"] == 6
    assert all(t["detail"]["design_sample"] for t in traces)

    # Reviewing what Review shows is what unlocks the compiler.
    assert client.post("/web-api/review", json={"ids": sorted(unreviewed)}).status_code == 200
    assert client.get("/web-api/state").json()["can_compile"]


def test_app_shell_stamps_its_asset_digest() -> None:
    """A deploy that changes the client without changing its URL reaches nobody."""
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app
    from coletar.inspector.web import _STATIC

    # `_app_html` reads the request host: the sign-in gate is loopback-only, so
    # the shell cannot be rendered without knowing who asked.
    html = TestClient(app, base_url="http://localhost").get("/app").text
    assert "__ASSETS__" not in html
    digest = re.search(r"product\.js\?v=([0-9a-f]{12})", html)
    assert digest is not None
    assert f"product.css?v={digest.group(1)}" in html
    assert (_STATIC / "product.js").exists()


@pytest.mark.asyncio
async def test_design_sample_loads_over_retired_only_history(live_store: None) -> None:
    """A workspace whose objects are all retired has nothing on its screens.

    That is exactly the workspace the examples exist for, and seeding it appends
    rather than deleting — the retired object and its events are still there
    afterwards.
    """
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app
    from coletar.store import build_store

    store = build_store()
    old = Memory.from_write("Cleared out of the workspace")
    await store.put_object(TENANT, old)
    client = TestClient(app, base_url="http://localhost")
    assert client.post("/web-api/sample").status_code == 409

    await store.retire_object(TENANT, old.id, reason="test_cleared")
    assert client.post("/web-api/sample").status_code == 200

    survivor = await store.get_object(TENANT, old.id)
    assert survivor is not None and survivor.retired_at is not None
    assert "mem_northwind" in {o.id for o in await store.list_objects(TENANT, limit=100)}
