"""M4.1 — the two policy defects the §5.1 pipeline documented but did not have.

Both are candidate-generation bugs, and both were invisible in the aggregate: the
suite reported 85.9% hit@5 while restricted memories were reaching prompts and
corrections were failing half the time. That is the argument for a labelled set
split by category — a single number averages a security hole into noise.
"""

from __future__ import annotations

import pytest

from coletar.retrieval import retrieve
from coletar.schema.objects import Memory, Scope, ScopeType, Sensitivity
from coletar.store.memory import InMemoryStore
from conftest import TENANT

PROJECT = Scope(type=ScopeType.PROJECT, id="proj_ledger")


async def store_with(*objects: Memory) -> InMemoryStore:
    store = InMemoryStore()
    for obj in objects:
        await store.put_object(TENANT, obj)
    return store


# --- sensitivity: the filter both modules documented and neither implemented ----


@pytest.mark.asyncio
async def test_restricted_objects_never_reach_a_prompt() -> None:
    """`context.py` names a "scope / activity / sensitivity policy filter" and
    `ranking.py` forbids any reranker from bypassing sensitivity. Until M4.1 neither
    backend implemented it, so a restricted memory was returned by `retrieve()` and
    rendered into the injected block — reaching local system prompts, the MCP tool's
    response, and the browser bridge."""
    store = await store_with(
        Memory.from_write("SSN 123-45-6789", sensitivity=Sensitivity.RESTRICTED),
        Memory.from_write("Chris prefers tabs."),
    )
    result = await retrieve(store, TENANT, "what is the SSN")

    assert "123-45-6789" not in [o.content for o in result.objects]
    assert "123-45-6789" not in result.as_prompt_block()


@pytest.mark.asyncio
async def test_sensitive_and_personal_still_retrieve() -> None:
    """Only `restricted` is withheld. Over-filtering would quietly make the product
    useless for the personal context it exists to carry."""
    store = await store_with(
        Memory.from_write("Chris is in therapy on Tuesdays.", sensitivity=Sensitivity.SENSITIVE),
        Memory.from_write("Chris lives in DC.", sensitivity=Sensitivity.PERSONAL),
    )
    found = [o.content for o in (await retrieve(store, TENANT, "therapy Tuesdays DC")).objects]
    assert any("therapy" in c for c in found)
    assert any("DC" in c for c in found)


@pytest.mark.asyncio
async def test_the_inspector_can_still_see_everything() -> None:
    """A page whose whole job is showing a user their own graph is the one caller
    that must not be filtered — otherwise restricted objects become unreviewable,
    and an object nobody can review is one nobody can delete."""
    store = await store_with(
        Memory.from_write("SSN 123-45-6789", sensitivity=Sensitivity.RESTRICTED)
    )
    assert await store.search(TENANT, "SSN", include_restricted=True)
    assert not await store.search(TENANT, "SSN")


# --- supersession-aware candidate generation -----------------------------------


@pytest.mark.asyncio
async def test_a_query_naming_the_old_value_returns_the_correction() -> None:
    """The 50% correction rate, as one case.

    "Is Chris still at Acme?" only matches the sentence being retired — the
    correction says Globex and never mentions Acme. Excluding superseded objects
    from candidate generation meant the reranker never saw the match, so no amount
    of reranking could have fixed it.
    """
    stale = Memory.from_write("Chris works at Acme Corp.")
    store = await store_with(stale)
    correction = Memory.from_write("Chris works at Globex.", supersedes=stale.id)
    await store.put_object(TENANT, correction)

    found = [o.content for o in (await retrieve(store, TENANT, "is Chris still at Acme")).objects]
    assert "Chris works at Globex." in found
    # The stale object is a candidate, never an answer.
    assert "Chris works at Acme Corp." not in found


@pytest.mark.asyncio
async def test_a_chain_resolves_to_what_is_true_now() -> None:
    """A fact corrected twice must not resolve to the middle of its own history."""
    v1 = Memory.from_write("Chris works at Acme Corp.")
    store = await store_with(v1)
    v2 = Memory.from_write("Chris works at Globex.", supersedes=v1.id)
    await store.put_object(TENANT, v2)
    v3 = Memory.from_write("Chris consults independently.", supersedes=v2.id)
    await store.put_object(TENANT, v3)

    found = [o.content for o in (await retrieve(store, TENANT, "does Chris work at Acme")).objects]
    assert "Chris consults independently." in found
    assert "Chris works at Globex." not in found
    assert "Chris works at Acme Corp." not in found


@pytest.mark.asyncio
async def test_one_hit_per_object_however_many_ancestors_matched() -> None:
    store = await store_with(Memory.from_write("Chris works at Acme Corp in Boston."))
    objects = await store.list_objects(TENANT, limit=10)
    correction = Memory.from_write("Chris consults independently.", supersedes=objects[0].id)
    await store.put_object(TENANT, correction)

    hits = await store.search(TENANT, "Acme Boston Chris")
    assert len({hit.obj.id for hit in hits}) == len(hits)


@pytest.mark.asyncio
async def test_policy_applies_to_the_object_returned_not_the_one_that_matched() -> None:
    """A stale ancestor must not decide who may read its replacement. The redirect
    is a recall mechanism; it is not a way around the policy filter."""
    stale = Memory.from_write("Chris works at Acme Corp.")
    store = await store_with(stale)
    correction = Memory.from_write(
        "Chris works at Globex, badge 44821.",
        supersedes=stale.id,
        sensitivity=Sensitivity.RESTRICTED,
    )
    await store.put_object(TENANT, correction)

    found = [o.content for o in (await retrieve(store, TENANT, "is Chris still at Acme")).objects]
    assert found == []


@pytest.mark.asyncio
async def test_a_retired_replacement_is_not_resurrected_by_its_ancestor() -> None:
    from datetime import UTC, datetime

    stale = Memory.from_write("Chris works at Acme Corp.")
    store = await store_with(stale)
    correction = Memory.from_write("Chris works at Globex.", supersedes=stale.id)
    await store.put_object(TENANT, correction)
    await store.retire_object(TENANT, correction.id, reason="test")

    assert await store.search(TENANT, "is Chris still at Acme") == []
    assert datetime.now(UTC)  # keeps the import honest
