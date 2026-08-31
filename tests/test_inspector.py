"""M5.3 — Inspector operations and the compile gate.

The requirement this file defends: nothing compiles until a human has seen what it
says. The subtle half is that a review is a statement about an object *as it was
then*, so the interesting tests are the ones where an approval has to stop counting.
"""

from __future__ import annotations

import pytest

from coletar.inspector.review import (
    InspectorError,
    edit,
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

    response = TestClient(app).get(path)
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

    await build_store().put_object(
        TENANT, Memory.from_write('<script>alert("x")</script>')
    )
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

    client = TestClient(app)
    response = client.post(
        "/merge", data={"survivor_id": obj.id, "absorbed_id": obj.id}, follow_redirects=True
    )
    assert "cannot be merged into itself" in response.text
