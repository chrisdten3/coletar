"""M3.4: reading what Claude Code writes to disk.

Every fixture here is synthesised. The tests never read a real transcript — those are
the developer's actual conversations, and a test suite has no business in them.

The shape is taken from a real file, though, and one number from it drives the whole
module: of 930 records with `type: "user"`, **873 were `tool_result`**. Ninety-four
percent of what looks like "the user said" is tool output being fed back. A parser
that trusted the record type would fill the graph with grep results.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coletar.acquisition import Turn, import_sessions, iter_turns, scope_for, session_files
from coletar.retrieval.embedding import HashingEmbedder
from coletar.schema.events import EventType
from coletar.schema.objects import ScopeType
from coletar.store.memory import InMemoryStore
from conftest import TENANT

CWD = "/Users/someone/Developer/ledger"


def _record(**overrides) -> dict:
    base = {
        "type": "user",
        "cwd": CWD,
        "sessionId": "sess-1",
        "uuid": "uuid-1",
        "timestamp": "2026-08-29T12:00:00Z",
        "isSidechain": False,
        "userType": "external",
        "message": {"role": "user", "content": "I prefer fixed-point integers for money."},
    }
    base.update(overrides)
    return base


def _write(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore(embedder=HashingEmbedder(768))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "projects" / "-Users-someone-Developer-ledger").mkdir(parents=True)
    return tmp_path


# -- the discriminator ---------------------------------------------------------
def test_tool_results_are_never_mistaken_for_the_user_speaking(tmp_path):
    """The finding this module exists around. 94% of `type: "user"` records in a real
    session are tool output; treating them as the user's words would fill the graph
    with file contents and stack traces."""
    path = _write(
        tmp_path / "s.jsonl",
        [
            _record(message={"role": "user", "content": "I prefer tabs over spaces."}),
            _record(message={"role": "user", "content": [
                {"type": "tool_result", "content": "I prefer to return exit code 1"}
            ]}),
            _record(message={"role": "user", "content": [
                {"type": "tool_result", "content": "def main(): ..."}
            ]}),
        ],
    )
    assert [t.text for t in iter_turns(path)] == ["I prefer tabs over spaces."]


def test_text_blocks_are_the_user_but_images_and_tool_results_are_not():
    """A mixed content list is normal: someone pastes an image with a question."""
    path = Path(__file__).parent / "_mixed.jsonl"
    try:
        _write(path, [_record(message={"role": "user", "content": [
            {"type": "image", "source": {}},
            {"type": "text", "text": "I never use an ORM."},
            {"type": "tool_result", "content": "irrelevant output"},
        ]})])
        assert [t.text for t in iter_turns(path)] == ["I never use an ORM."]
    finally:
        path.unlink(missing_ok=True)


def test_assistant_turns_are_never_read(tmp_path):
    """A model's statements about the user are inference, not testimony — the same
    rule the proxy follows."""
    path = _write(tmp_path / "s.jsonl", [
        _record(type="assistant", message={"role": "assistant",
                                           "content": "I prefer fixed-point integers."}),
        _record(type="system"),
        _record(type="ai-title", aiTitle="Money representation"),
    ])
    assert list(iter_turns(path)) == []


def test_sidechain_turns_belong_to_a_sub_agent_not_the_user(tmp_path):
    path = _write(tmp_path / "s.jsonl", [
        _record(isSidechain=True, message={"role": "user", "content": "I prefer tabs."}),
    ])
    assert list(iter_turns(path)) == []


def test_a_partially_written_line_is_skipped_not_fatal(tmp_path):
    """A watcher reads a file the editor is still appending to."""
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(_record()) + "\n" + '{"type": "user", "mess')
    assert len(list(iter_turns(path))) == 1


# -- scope ---------------------------------------------------------------------
def test_the_working_directory_becomes_the_project_scope():
    """A fact stated while working on one repository should not surface as global
    context in an unrelated one."""
    scope = scope_for("/Users/someone/Developer/ledger")
    assert scope.type is ScopeType.PROJECT
    assert scope.id == "proj_ledger"


def test_the_scope_id_does_not_record_where_on_disk_you_work():
    assert "users" not in (scope_for("/Users/someone/Developer/ledger").id or "")


@pytest.mark.parametrize("cwd", [None, "", "/", "/a"])
def test_an_unusable_directory_falls_back_to_global(cwd):
    assert scope_for(cwd).type is ScopeType.GLOBAL


def test_project_scoping_can_be_declined():
    assert scope_for(CWD, project_scopes=False).type is ScopeType.GLOBAL


# -- import --------------------------------------------------------------------
async def test_import_extracts_only_durable_statements(store, root):
    _write(root / "projects" / "-Users-someone-Developer-ledger" / "a.jsonl", [
        _record(message={"role": "user", "content": "I never use an ORM in this project."}),
        _record(message={"role": "user", "content": "what does this function do?"}),
        _record(message={"role": "user", "content": [
            {"type": "tool_result", "content": "I prefer verbose output"}
        ]}),
    ])
    report = await import_sessions(store, TENANT, root=root)

    stored = await store.list_objects(TENANT, limit=50)
    assert [o.content for o in stored] == ["I never use an ORM in this project"]
    assert report.turns == 2  # the question counts as a turn; it just stores nothing
    assert report.stored == 1


async def test_imported_memories_carry_their_source(store, root):
    _write(root / "projects" / "-Users-someone-Developer-ledger" / "a.jsonl", [
        _record(message={"role": "user", "content": "I never use an ORM in this project."}),
    ])
    await import_sessions(store, TENANT, root=root)

    obj = (await store.list_objects(TENANT))[0]
    assert obj.provenance.provider.value == "claude"
    assert obj.extraction_method.value == "explicit_statement"
    assert "sess-1" in obj.provenance.source_object_ids
    assert obj.scope.id == "proj_ledger"

    event = (await store.list_events(TENANT, object_id=obj.id))[0]
    assert event.type is EventType.CONNECTOR_WRITE
    assert event.detail["surface"] == "claude-code"
    # The transcript stays on disk; the event points at it rather than copying it.
    assert event.detail["session_file"] == "a.jsonl"


async def test_a_second_run_reads_only_what_is_new(store, root):
    path = root / "projects" / "-Users-someone-Developer-ledger" / "a.jsonl"
    _write(path, [_record(message={"role": "user", "content": "I never use an ORM here."})])
    first = await import_sessions(store, TENANT, root=root)
    second = await import_sessions(store, TENANT, root=root)

    assert first.turns == 1
    assert second.turns == 0, "an unchanged file should not be re-read"

    with path.open("a") as fh:
        fh.write(json.dumps(_record(
            message={"role": "user", "content": "I always use strict mode with mypy."}
        )) + "\n")
    third = await import_sessions(store, TENANT, root=root)
    assert third.turns == 1
    assert len(await store.list_objects(TENANT, limit=50)) == 2


async def test_rereading_from_scratch_does_not_duplicate(store, root):
    """Safe to re-run after the extractor improves — that is what makes reprocessing
    a real option rather than a one-way door."""
    _write(root / "projects" / "-Users-someone-Developer-ledger" / "a.jsonl", [
        _record(message={"role": "user", "content": "I never use an ORM in this project."}),
    ])
    await import_sessions(store, TENANT, root=root)
    again = await import_sessions(store, TENANT, root=root, rescan=True)

    assert again.stored == 0 and again.corroborated == 1
    assert len(await store.list_objects(TENANT, limit=50)) == 1


async def test_sessions_in_different_directories_land_in_different_scopes(store, root):
    other = root / "projects" / "-Users-someone-Developer-atlas"
    other.mkdir(parents=True)
    _write(root / "projects" / "-Users-someone-Developer-ledger" / "a.jsonl", [
        _record(message={"role": "user", "content": "I never use an ORM in this project."}),
    ])
    _write(other / "b.jsonl", [
        _record(cwd="/Users/someone/Developer/atlas",
                message={"role": "user", "content": "I always use Rust for this one."}),
    ])
    await import_sessions(store, TENANT, root=root)

    scopes = {o.scope.id for o in await store.list_objects(TENANT, limit=50)}
    assert scopes == {"proj_ledger", "proj_atlas"}


def test_no_transcripts_is_not_an_error(tmp_path):
    assert session_files(tmp_path) == []


def test_turn_provenance_skips_missing_ids():
    turn = Turn(text="x", cwd=None, session_id="s", uuid=None,
                timestamp=None, source=Path("a.jsonl"))
    assert turn.provenance_ids == ["s"]


def test_the_extractor_is_narrow_by_design_and_that_shows_up_here():
    """"I always run mypy" is not caught; "I always use strict mode" is. The trigger
    list is deliberately small — precision over recall — so an import reads far more
    turns than it stores, and that ratio is the feature rather than a shortfall.
    Recorded because it looks like a bug the first time an import returns almost
    nothing from a long transcript.
    """
    import asyncio

    from coletar.extraction import extract_memories

    async def run(text: str) -> int:
        return len(await extract_memories(user_text=text))

    assert asyncio.run(run("I always run mypy in strict mode.")) == 0
    assert asyncio.run(run("I always use strict mode with mypy.")) == 1
