"""M8 — the Markdown mirror.

Basic Memory stores Markdown as the source of truth. This mirrors *to* Markdown and
keeps the typed graph canonical, because supersession, provenance and an immutable
event log are things a directory of files cannot make true — and they are exactly
what the audit case is buying.

The tests that matter are the ones that would fail if the mirror ever became a second
source of truth.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coletar.mirror import (
    content_hash,
    frontmatter,
    mirror,
    parse_mirrored,
    pull_edits,
    render_object,
)
from coletar.schema.events import EventType
from coletar.schema.objects import (
    Locality,
    LocalityMode,
    Memory,
    MemoryKind,
    Provider,
    Scope,
    ScopeType,
)
from coletar.store.memory import InMemoryStore
from conftest import TENANT


async def seeded() -> tuple[InMemoryStore, Memory]:
    store = InMemoryStore()
    fact = Memory.from_write(
        "Chris prefers fixed-point integers over doubles for money.",
        kind=MemoryKind.PREFERENCE,
    )
    await store.put_object(TENANT, fact)
    return store, fact


# --- it is a projection, and stays one -------------------------------------------


@pytest.mark.asyncio
async def test_mirroring_writes_no_events(tmp_path: Path) -> None:
    """A projection that recorded itself would make the log describe the observer."""
    store, _ = await seeded()
    before = len(await store.list_events(TENANT, limit=500))
    await mirror(store, TENANT, tmp_path)
    assert len(await store.list_events(TENANT, limit=500)) == before


@pytest.mark.asyncio
async def test_mirroring_twice_changes_nothing(tmp_path: Path) -> None:
    """Byte-identical output means a vault can live in git and a diff means the graph
    moved, not that you ran the command again."""
    store, _ = await seeded()
    first = await mirror(store, TENANT, tmp_path)
    assert first.written, "the first run should write something"
    snapshot = {p: p.read_bytes() for p in tmp_path.rglob("*.md")}

    second = await mirror(store, TENANT, tmp_path)
    assert second.written == []
    assert second.unchanged == len(first.written)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.md")} == snapshot


@pytest.mark.asyncio
async def test_the_vault_says_it_is_not_the_source_of_truth(tmp_path: Path) -> None:
    store, _ = await seeded()
    await mirror(store, TENANT, tmp_path)
    readme = (tmp_path / "README.md").read_text()
    assert "projection, not the" in readme and "source of truth" in readme
    assert "--pull" in readme


# --- what a file has to carry to be a record --------------------------------------


@pytest.mark.asyncio
async def test_every_file_carries_its_own_provenance(tmp_path: Path) -> None:
    """§4: an object we cannot explain should not exist — and that has to survive the
    trip out of the database. A mirrored file with no provenance is a note, not a
    record."""
    store, fact = await seeded()
    await mirror(store, TENANT, tmp_path)

    body = (tmp_path / "objects" / "memory" / f"{fact.id}.md").read_text()
    meta, _ = parse_mirrored(body)
    assert meta["coletar_id"] == fact.id
    assert meta["extraction_method"]
    assert meta["origin"].count("/") == 1
    assert meta["confidence"]
    assert meta["version"] == "1"


@pytest.mark.asyncio
async def test_a_correction_chain_is_navigable_in_obsidian(tmp_path: Path) -> None:
    """The one thing a file tree does better than a database: you can see the chain."""
    store, stale = await seeded()
    correction = Memory.from_write("Chris now uses decimals.", supersedes=stale.id)
    await store.put_object(TENANT, correction)
    await mirror(store, TENANT, tmp_path)

    body = (tmp_path / "objects" / "memory" / f"{correction.id}.md").read_text()
    assert f"Supersedes [[{stale.id}]]" in body


@pytest.mark.asyncio
async def test_retired_objects_are_mirrored_and_labelled(tmp_path: Path) -> None:
    """Constraint 6. The graph never hard-deletes, so neither does its mirror."""
    store, fact = await seeded()
    await store.retire_object(TENANT, fact.id, reason="test")
    await mirror(store, TENANT, tmp_path)

    body = (tmp_path / "objects" / "memory" / f"{fact.id}.md").read_text()
    assert "Retired" in body
    assert "Kept for provenance" in body


def test_locality_survives_the_trip() -> None:
    private = Memory.from_write(
        "Only my local model.",
        locality=Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset({Provider.LOCAL})),
    )
    assert frontmatter(private)["locality"] == "local_only:local"


def test_content_that_would_break_yaml_is_quoted() -> None:
    tricky = Memory.from_write("x", scope=Scope(type=ScopeType.PROJECT, id="a: b"))
    rendered = render_object(tricky)
    meta, _ = parse_mirrored(rendered)
    assert meta["scope"] == "project:a: b"


@pytest.mark.asyncio
async def test_the_event_log_is_mirrored_by_month(tmp_path: Path) -> None:
    store, _ = await seeded()
    report = await mirror(store, TENANT, tmp_path)
    months = list((tmp_path / "events").glob("*.md"))
    assert months and report.events > 0
    assert "object.created" in months[0].read_text()


# --- pulling edits back, through the boundary -------------------------------------


@pytest.mark.asyncio
async def test_an_edit_lands_as_a_real_event_not_a_silent_write(tmp_path: Path) -> None:
    """The half that keeps this honest.

    A two-way sync writing straight to the store would put a change in the graph with
    no event behind it — the one thing the substrate must not allow, and what would
    make the audit trail a decoration.
    """
    store, fact = await seeded()
    await mirror(store, TENANT, tmp_path)

    path = tmp_path / "objects" / "memory" / f"{fact.id}.md"
    path.write_text(path.read_text().replace(fact.content, "Chris now prefers decimals."))

    report = await pull_edits(store, TENANT, tmp_path)
    assert report.applied == 1

    stored = await store.get_object(TENANT, fact.id)
    assert stored is not None and stored.content == "Chris now prefers decimals."
    assert stored.version == 2

    events = await store.list_events(TENANT, object_id=fact.id, limit=20)
    revision = next(e for e in events if e.type is EventType.OBJECT_UPDATED)
    assert revision.before is not None and revision.after is not None
    assert revision.before["content"] == fact.content
    assert revision.after["content"] == "Chris now prefers decimals."


@pytest.mark.asyncio
async def test_untouched_files_are_not_rewritten(tmp_path: Path) -> None:
    """Detection is by content hash, not mtime: copying a vault between machines
    rewrites every mtime and would otherwise look like you edited everything."""
    store, _ = await seeded()
    await mirror(store, TENANT, tmp_path)
    for path in tmp_path.rglob("*.md"):
        path.touch()

    report = await pull_edits(store, TENANT, tmp_path)
    assert report.edited == 0 and report.applied == 0


@pytest.mark.asyncio
async def test_a_stale_file_is_refused_rather_than_resolved(tmp_path: Path) -> None:
    """The graph moved on since this file was written. Silently picking a winner is
    how someone's edit disappears."""
    store, fact = await seeded()
    await mirror(store, TENANT, tmp_path)

    stored = await store.get_object(TENANT, fact.id)
    assert stored is not None
    stored.content = "Changed in the graph, not in the vault."
    await store.put_object(TENANT, stored)

    path = tmp_path / "objects" / "memory" / f"{fact.id}.md"
    path.write_text(path.read_text().replace(fact.content, "Changed in the vault too."))

    report = await pull_edits(store, TENANT, tmp_path)
    assert report.conflicts == [fact.id]
    assert report.applied == 0


@pytest.mark.asyncio
async def test_dry_run_reports_without_changing_anything(tmp_path: Path) -> None:
    store, fact = await seeded()
    await mirror(store, TENANT, tmp_path)
    path = tmp_path / "objects" / "memory" / f"{fact.id}.md"
    path.write_text(path.read_text().replace(fact.content, "Edited."))

    report = await pull_edits(store, TENANT, tmp_path, dry_run=True)
    assert report.applied == 1
    stored = await store.get_object(TENANT, fact.id)
    assert stored is not None and stored.content == fact.content


@pytest.mark.asyncio
async def test_a_file_from_somewhere_else_is_reported_not_guessed_at(tmp_path: Path) -> None:
    store, _ = await seeded()
    await mirror(store, TENANT, tmp_path)
    (tmp_path / "objects" / "memory" / "my-own-note.md").write_text("# Just my notes\n")

    report = await pull_edits(store, TENANT, tmp_path)
    assert "objects/memory/my-own-note.md" in report.unknown


def test_content_hash_is_stable() -> None:
    assert content_hash("a") == content_hash("a")
    assert content_hash("a") != content_hash("b")
