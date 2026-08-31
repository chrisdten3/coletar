"""M9 — as-of queries.

Two of the four questions a compliance reader asks cannot be answered by any memory
layer that stores only the current value: *what did this say on 3 March*, and *what
changed between these dates*. They need an immutable log carrying full before/after
state, which is why constraint 5 exists.

The subtle test here is `test_supersession_is_evaluated_as_of_then`. Getting that
wrong makes the whole feature confidently wrong rather than visibly broken.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from coletar.schema.objects import Memory, MemoryKind, Scope, ScopeType
from coletar.store.memory import InMemoryStore
from coletar.temporal import changes_between, graph_as_of, search_as_of
from conftest import TENANT

PROJECT = Scope(type=ScopeType.PROJECT, id="proj_policy")


async def mark() -> datetime:
    """A timestamp strictly between two writes."""
    await asyncio.sleep(0.01)
    now = datetime.now(UTC)
    await asyncio.sleep(0.01)
    return now


# --- the question nobody else can answer -----------------------------------------


@pytest.mark.asyncio
async def test_what_a_policy_said_at_a_past_moment() -> None:
    store = InMemoryStore()
    policy = Memory.from_write(
        "Expense approvals over $500 need a director.", kind=MemoryKind.FACT
    )
    await store.put_object(TENANT, policy)
    march = await mark()

    stored = await store.get_object(TENANT, policy.id)
    assert stored is not None
    stored.content = "Expense approvals over $2000 need a director."
    await store.put_object(TENANT, stored)

    then = await graph_as_of(store, TENANT, march)
    assert [o.content for o in then] == ["Expense approvals over $500 need a director."]

    now = await graph_as_of(store, TENANT, datetime.now(UTC))
    assert [o.content for o in now] == ["Expense approvals over $2000 need a director."]


@pytest.mark.asyncio
async def test_supersession_is_evaluated_as_of_then_not_now() -> None:
    """The subtle half, and the one that makes this feature trustworthy.

    A fact corrected last week was still the current answer in March. Filtering the
    reconstructed graph against *today's* supersession would report that March
    believed something it did not — confidently wrong rather than visibly broken,
    which is the worst kind of answer for an audit.
    """
    store = InMemoryStore()
    original = Memory.from_write("Retention is 3 years.")
    await store.put_object(TENANT, original)
    march = await mark()

    correction = Memory.from_write("Retention is 7 years.", supersedes=original.id)
    await store.put_object(TENANT, correction)

    then = [o.content for o in await graph_as_of(store, TENANT, march)]
    assert then == ["Retention is 3 years."]

    now = [o.content for o in await graph_as_of(store, TENANT, datetime.now(UTC))]
    assert now == ["Retention is 7 years."]


@pytest.mark.asyncio
async def test_a_retired_object_was_still_live_before_retirement() -> None:
    store = InMemoryStore()
    rule = Memory.from_write("Contractors need a signed NDA.")
    await store.put_object(TENANT, rule)
    before = await mark()
    await store.retire_object(TENANT, rule.id, reason="policy withdrawn")

    assert [o.content for o in await graph_as_of(store, TENANT, before)] == [
        "Contractors need a signed NDA."
    ]
    assert await graph_as_of(store, TENANT, datetime.now(UTC)) == []


@pytest.mark.asyncio
async def test_before_anything_existed_the_answer_is_nothing() -> None:
    store = InMemoryStore()
    dawn = datetime.now(UTC) - timedelta(days=365)
    await store.put_object(TENANT, Memory.from_write("Anything."))
    assert await graph_as_of(store, TENANT, dawn) == []


# --- reconstruction reads the log, not the table ----------------------------------


@pytest.mark.asyncio
async def test_reconstruction_uses_the_log_so_the_answer_is_defensible() -> None:
    """If the table and the log ever disagree, the log is the thing you can show
    someone. The reconstruction is built from `after` snapshots alone."""
    store = InMemoryStore()
    fact = Memory.from_write("Original text.")
    await store.put_object(TENANT, fact)

    events = await store.list_events(TENANT, object_id=fact.id, limit=10)
    assert any(e.after and e.after["content"] == "Original text." for e in events)

    rebuilt = await graph_as_of(store, TENANT, datetime.now(UTC))
    assert [o.content for o in rebuilt] == ["Original text."]


# --- searching the past -----------------------------------------------------------


@pytest.mark.asyncio
async def test_search_finds_what_the_policy_said_then() -> None:
    store = InMemoryStore()
    policy = Memory.from_write("Expense approvals over $500 need a director.")
    await store.put_object(TENANT, policy)
    await store.put_object(TENANT, Memory.from_write("Coffee is reimbursable."))
    march = await mark()

    stored = await store.get_object(TENANT, policy.id)
    assert stored is not None
    stored.content = "Expense approvals need no director."
    await store.put_object(TENANT, stored)

    hits = await search_as_of(store, TENANT, "expense approvals director", march)
    assert hits
    assert "$500" in hits[0].obj.content


@pytest.mark.asyncio
async def test_as_of_search_is_lexical_and_says_so() -> None:
    """Documented rather than quietly different: the vector index holds current
    state, so a reader should be able to see that this is not the live hybrid."""
    store = InMemoryStore()
    await store.put_object(TENANT, Memory.from_write("Retention is seven years."))
    hits = await search_as_of(store, TENANT, "retention seven", datetime.now(UTC))
    assert hits
    assert hits[0].components.vector == 0.0
    assert str(hits[0].components.source) == "lexical"


@pytest.mark.asyncio
async def test_as_of_search_respects_scope() -> None:
    store = InMemoryStore()
    await store.put_object(TENANT, Memory.from_write("Global retention is 3 years."))
    await store.put_object(
        TENANT, Memory.from_write("Policy project retention is 7 years.", scope=PROJECT)
    )
    now = datetime.now(UTC)

    scoped = await search_as_of(store, TENANT, "retention years", now, scope=PROJECT)
    contents = {hit.obj.content for hit in scoped}
    # A project sees its own and the global; never another project's.
    assert any("Policy project" in c for c in contents)
    assert any("Global retention" in c for c in contents)


# --- what changed between two dates ------------------------------------------------


@pytest.mark.asyncio
async def test_changes_between_two_dates_reads_as_a_diff() -> None:
    store = InMemoryStore()
    start = await mark()

    policy = Memory.from_write("Retention is 3 years.")
    await store.put_object(TENANT, policy)
    stored = await store.get_object(TENANT, policy.id)
    assert stored is not None
    stored.content = "Retention is 7 years."
    await store.put_object(TENANT, stored)

    end = datetime.now(UTC)
    changes = await changes_between(store, TENANT, start, end)

    assert [c.kind for c in changes] == ["added", "changed"]
    assert changes[1].before == "Retention is 3 years."
    assert changes[1].after == "Retention is 7 years."
    assert changes[1].actor


@pytest.mark.asyncio
async def test_a_window_with_no_changes_is_empty_not_an_error() -> None:
    store = InMemoryStore()
    await store.put_object(TENANT, Memory.from_write("Something."))
    quiet_start = datetime.now(UTC) + timedelta(days=1)
    quiet_end = quiet_start + timedelta(days=1)
    assert await changes_between(store, TENANT, quiet_start, quiet_end) == []


@pytest.mark.asyncio
async def test_a_retirement_is_reported_as_one() -> None:
    store = InMemoryStore()
    start = await mark()
    rule = Memory.from_write("An old rule.")
    await store.put_object(TENANT, rule)
    await store.retire_object(TENANT, rule.id, reason="withdrawn")

    changes = await changes_between(store, TENANT, start, datetime.now(UTC))
    assert "retired" in {c.kind for c in changes}
