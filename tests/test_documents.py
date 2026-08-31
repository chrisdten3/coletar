"""M9 — source documents as provenance.

"Prove today's answer derives from today's policy" should be a link rather than an
argument. An auditor's next question after *what did we believe* is always *says who*.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from coletar.documents import (
    MAX_DOCUMENT_CHARS,
    AttachedDocument,
    DocumentError,
    attach_document,
    cite,
    sources_for,
)
from coletar.schema.objects import Memory, ObjectType
from coletar.store.memory import InMemoryStore
from coletar.temporal import graph_as_of
from conftest import TENANT


def policy(tmp_path: Path, text: str = "Retention is 7 years.\n") -> Path:
    path = tmp_path / "handbook.md"
    path.write_text(text)
    return path


# --- no new table -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_document_is_an_artifact_not_a_new_kind_of_thing(tmp_path: Path) -> None:
    """§2: a property applying to one workflow does not earn a column. A source
    document *is* an artifact, and provenance already links a fact to it."""
    store = InMemoryStore()
    attached = await attach_document(store, TENANT, policy(tmp_path))

    stored = await store.get_object(TENANT, attached.object_id)
    assert stored is not None
    assert stored.type is ObjectType.ARTIFACT
    assert stored.payload["filename"] == "handbook.md"
    assert stored.payload["digest"] == attached.digest


@pytest.mark.asyncio
async def test_a_fact_can_point_at_the_document_it_came_from(tmp_path: Path) -> None:
    store = InMemoryStore()
    document = await attach_document(store, TENANT, policy(tmp_path))
    fact = Memory.from_write("Retention is 7 years.")
    await store.put_object(TENANT, fact)

    await cite(store, TENANT, fact.id, document.object_id)

    sources = await sources_for(store, TENANT, fact.id)
    assert [s.payload["filename"] for s in sources] == ["handbook.md"]


@pytest.mark.asyncio
async def test_citing_appends_rather_than_replaces(tmp_path: Path) -> None:
    """A fact can rest on more than one source, and overwriting would quietly discard
    the first thing that justified it."""
    store = InMemoryStore()
    first = await attach_document(store, TENANT, policy(tmp_path))
    second_path = tmp_path / "addendum.md"
    second_path.write_text("Retention for contractors is 3 years.\n")
    second = await attach_document(store, TENANT, second_path)

    fact = Memory.from_write("Retention rules.")
    await store.put_object(TENANT, fact)
    await cite(store, TENANT, fact.id, first.object_id)
    await cite(store, TENANT, fact.id, second.object_id)

    assert len(await sources_for(store, TENANT, fact.id)) == 2


@pytest.mark.asyncio
async def test_citing_the_same_document_twice_is_a_no_op(tmp_path: Path) -> None:
    store = InMemoryStore()
    document = await attach_document(store, TENANT, policy(tmp_path))
    fact = Memory.from_write("Retention is 7 years.")
    await store.put_object(TENANT, fact)

    await cite(store, TENANT, fact.id, document.object_id)
    await cite(store, TENANT, fact.id, document.object_id)
    assert len(await sources_for(store, TENANT, fact.id)) == 1


# --- identity is content, not filename --------------------------------------------


@pytest.mark.asyncio
async def test_re_attaching_the_same_document_is_recognised(tmp_path: Path) -> None:
    """Re-downloading a policy should not produce a second source for the same text."""
    store = InMemoryStore()
    first = await attach_document(store, TENANT, policy(tmp_path))

    renamed = tmp_path / "handbook-final-v2.md"
    renamed.write_text(policy(tmp_path).read_text())
    second = await attach_document(store, TENANT, renamed)

    assert second.object_id == first.object_id
    assert second.already_held


@pytest.mark.asyncio
async def test_an_edited_document_is_a_different_source(tmp_path: Path) -> None:
    store = InMemoryStore()
    first = await attach_document(store, TENANT, policy(tmp_path))
    revised = await attach_document(
        store, TENANT, policy(tmp_path, "Retention is 10 years.\n")
    )
    assert revised.object_id != first.object_id


# --- documents are in force for a period too ---------------------------------------


@pytest.mark.asyncio
async def test_a_document_carries_its_own_validity(tmp_path: Path) -> None:
    """"Which version of the handbook applied in March" asks about the document, not
    only about what was extracted from it."""
    store = InMemoryStore()
    await attach_document(
        store,
        TENANT,
        policy(tmp_path, "2025 handbook.\n"),
        valid_until=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await attach_document(
        store,
        TENANT,
        policy(tmp_path, "2026 handbook.\n"),
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )

    now = datetime.now(UTC)
    in_2025 = await graph_as_of(
        store, TENANT, now, in_force_at=datetime(2025, 6, 1, tzinfo=UTC)
    )
    assert [o.content.strip() for o in in_2025] == ["2025 handbook."]


# --- refusals a person can act on ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_pdf_is_refused_with_a_reason_not_mangled(tmp_path: Path) -> None:
    """A parser returning raw bytes as "content" produces a fact whose source is
    unreadable, which is worse than no source at all."""
    pdf = tmp_path / "policy.pdf"
    pdf.write_bytes(b"%PDF-1.7 binary")
    with pytest.raises(DocumentError, match="not a text format"):
        await attach_document(InMemoryStore(), TENANT, pdf)


@pytest.mark.asyncio
async def test_an_oversized_document_is_refused(tmp_path: Path) -> None:
    """A policy that would fill a context window is one nobody will read in the
    Inspector, and the Inspector is where these get approved."""
    huge = tmp_path / "everything.md"
    huge.write_text("x" * (MAX_DOCUMENT_CHARS + 1))
    with pytest.raises(DocumentError, match="over the"):
        await attach_document(InMemoryStore(), TENANT, huge)


@pytest.mark.asyncio
async def test_an_empty_or_missing_file_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "empty.md"
    empty.write_text("   \n")
    with pytest.raises(DocumentError, match="is empty"):
        await attach_document(InMemoryStore(), TENANT, empty)
    with pytest.raises(DocumentError, match="does not exist"):
        await attach_document(InMemoryStore(), TENANT, tmp_path / "nope.md")


@pytest.mark.asyncio
async def test_citing_across_tenants_is_refused(tmp_path: Path) -> None:
    from coletar.schema.tenancy import tenant_id

    store = InMemoryStore()
    document = await attach_document(store, TENANT, policy(tmp_path))
    with pytest.raises(DocumentError, match="no object"):
        await cite(store, tenant_id("tenant_other"), "mem_x", document.object_id)


def test_the_attached_record_is_readable() -> None:
    record = AttachedDocument("art_1", "handbook.md", "abc123", 42, False)
    assert record.filename == "handbook.md"
