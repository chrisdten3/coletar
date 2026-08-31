"""Importing a real-shaped Claude export: memories, projects, conversations.

The fixture mirrors the structure of an actual export — verified against one — rather
than a guess: `memories/*.json` with `memory_files`, `conversations_memory` and
`project_memories`; `projects/*.json` with `prompt_template` and `docs`;
`conversations.json` with `chat_messages`.

The claim these tests defend is that **memories are not mined**. Claude has already
extracted them, and running the regex extractor over an extracted fact recovers about
a third of it for no gain.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coletar.acquisition.claude_export import (
    import_bundle,
    read_memories,
    read_projects,
)
from coletar.schema.objects import ExtractionMethod, ObjectType, Provider, ScopeType
from coletar.store.memory import InMemoryStore
from conftest import TENANT

PROJECT_ID = "019df086-b3c9-7312-a78a-4226cc054807"


@pytest.fixture
def export(tmp_path: Path) -> Path:
    (tmp_path / "memories").mkdir()
    (tmp_path / "projects").mkdir()

    (tmp_path / "memories" / "mem.json").write_text(
        json.dumps(
            {
                "account_uuid": "acct",
                "memory_files": [
                    {
                        "path": "/profile.md",
                        "content": "# Profile\n\n- Chris is a backend engineer.\n"
                        "- He works in Pacific time.\n",
                        "updated_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "path": "/topics/work-style.md",
                        "content": "- Prefers short replies with no preamble.\n",
                        "updated_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "path": "/areas/bracket-model.md",
                        "content": "- The bracket model uses Poisson scoring rates.\n",
                        "updated_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "path": "/people/priya-nair.md",
                        "content": "- Priya runs the desk and prefers email.\n",
                        "updated_at": "2026-01-01T00:00:00Z",
                    },
                ],
                "conversations_memory": "- Chris ships C++20 for EventBook.\n",
                "project_memories": {
                    PROJECT_ID: "- The ledger service settled on double-entry.\n"
                },
            }
        )
    )
    (tmp_path / "projects" / "p1.json").write_text(
        json.dumps(
            {
                "uuid": PROJECT_ID,
                "name": "Ledger Research",
                "description": "Research on settlement timing.",
                "prompt_template": "Always show the failing test before proposing a fix.",
                "docs": [
                    {
                        "uuid": "d1",
                        "filename": "notes.md",
                        "content": "Settlement timing notes, at length.",
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        )
    )
    (tmp_path / "conversations.json").write_text(
        json.dumps(
            [
                {
                    "uuid": "c1",
                    "name": "A chat",
                    "created_at": "2026-01-01T00:00:00Z",
                    "chat_messages": [
                        {
                            "uuid": "m1",
                            "sender": "human",
                            "text": "I prefer fixed-point integers over doubles for money.",
                        },
                        {"uuid": "m2", "sender": "assistant", "text": "Understood."},
                    ],
                }
            ]
        )
    )
    return tmp_path


# --- memories are facts, not prose to mine ---------------------------------------


def test_memory_files_split_into_individual_facts(export: Path) -> None:
    """A file is a list of discrete facts under a heading. Importing it whole would
    produce a blob retrieval cannot rank and supersession cannot correct a line of."""
    lines = read_memories(export / "memories" / "mem.json")
    texts = [line.text for line in lines]

    assert "Chris is a backend engineer." in texts
    assert "He works in Pacific time." in texts
    # The markdown heading is not a fact.
    assert not any(text.startswith("#") for text in texts)


def test_claudes_own_filing_becomes_scope(export: Path) -> None:
    """`/areas/bracket-model.md` is project-shaped memory. Flattening several
    areas into one global pile loses the structure that makes a compile produce
    per-project containers instead of a single blob."""
    by_text = {line.text: line for line in read_memories(export / "memories" / "mem.json")}

    assert by_text["The bracket model uses Poisson scoring rates."].project_id == (
        "bracket-model"
    )
    assert by_text["Chris is a backend engineer."].project_id is None
    # Keyed by uuid, so this one is exact rather than inferred from a filename.
    assert by_text["The ledger service settled on double-entry."].project_id == PROJECT_ID


def test_areas_can_stay_global_when_asked(export: Path) -> None:
    lines = read_memories(export / "memories" / "mem.json", areas_as_projects=False)
    poisson = next(line for line in lines if "Poisson" in line.text)
    assert poisson.project_id is None


@pytest.mark.asyncio
async def test_memories_are_imported_whole_not_mined(export: Path) -> None:
    """The claim this module rests on.

    "Chris is a backend engineer" matches none of the extractor's first-person
    patterns, so mining would drop it. Claude already did the extraction.
    """
    from coletar.extraction import extract_memories

    assert await extract_memories(user_text="Chris is a backend engineer.") == []

    store = InMemoryStore()
    await import_bundle(store, TENANT, export, include_conversations=False)
    contents = {o.content for o in await store.list_objects(TENANT, limit=500)}
    assert "Chris is a backend engineer." in contents


# --- projects carry instructions and knowledge ------------------------------------


def test_a_project_carries_its_instructions_and_docs(export: Path) -> None:
    """`prompt_template` and `docs` are the same shape `ClaudeCompiler` emits going
    the other way, which is what makes the round trip symmetric."""
    records = read_projects(export / "projects")
    assert len(records) == 1
    assert records[0].prompt_template.startswith("Always show")
    assert records[0].docs[0].filename == "notes.md"


@pytest.mark.asyncio
async def test_the_import_types_each_kind_of_thing(export: Path) -> None:
    store = InMemoryStore()
    report = await import_bundle(store, TENANT, export)

    assert report.projects == 1
    assert report.instructions == 1
    assert report.project_docs == 1
    # 2 profile + 1 topics + 1 areas + 1 people + 1 conversations_memory
    # + 1 project_memories
    assert report.memories == 7
    assert report.conversations == 1

    objects = await store.list_objects(TENANT, limit=500)
    by_type: dict[ObjectType, int] = {}
    for obj in objects:
        by_type[obj.type] = by_type.get(obj.type, 0) + 1
    assert by_type[ObjectType.PROJECT] == 1
    assert by_type[ObjectType.ARTIFACT] == 1

    for obj in objects:
        assert obj.provenance.provider is Provider.CLAUDE
        assert obj.extraction_method is ExtractionMethod.ACCOUNT_EXPORT_PARSE
        assert obj.provenance.source_object_ids


@pytest.mark.asyncio
async def test_project_scoped_memory_lands_in_a_readable_scope(export: Path) -> None:
    """A scope named by uuid is one nobody will review, and the Inspector is where
    these get approved before anything compiles out."""
    store = InMemoryStore()
    await import_bundle(store, TENANT, export)

    scoped = [
        o
        for o in await store.list_objects(TENANT, limit=500)
        if o.scope.type is ScopeType.PROJECT
    ]
    assert scoped
    assert any(o.scope.id == "claude_ledger-research" for o in scoped)


# --- ordering ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversations_corroborate_rather_than_arrive_first(export: Path) -> None:
    """Ordering is deliberate: memories land at full fidelity, then mined prose
    corroborates. Reversed, the weaker reading wins and the good one arrives as a
    duplicate."""
    store = InMemoryStore()
    memories_only = await import_bundle(store, TENANT, export, include_conversations=False)

    fresh = InMemoryStore()
    everything = await import_bundle(fresh, TENANT, export)

    # Mining 1 conversation adds little on top of the memory export — the same shape
    # as the real export, where 1,229 turns added ~17 objects to 188.
    assert everything.created >= memories_only.created
    assert everything.conversation_turns == 1


@pytest.mark.asyncio
async def test_re_importing_the_same_export_creates_nothing(export: Path) -> None:
    store = InMemoryStore()
    first = await import_bundle(store, TENANT, export)
    second = await import_bundle(store, TENANT, export)

    assert second.created == 0
    assert second.corroborated >= first.created
