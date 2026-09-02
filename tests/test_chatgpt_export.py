"""M6.1 — parsing a ChatGPT export.

Two bars, and the second one is why the first is not enough on its own. Precision
must clear 85%, which the extractor does comfortably — but precision on four
extractions is not the same claim as precision on thirty-five, so recall is reported
beside it and the fixture is honest about what it is.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from coletar.acquisition.chatgpt_export import (
    ChatGPTExportError,
    active_branch,
    import_export,
    parse_conversation,
    read_export,
)
from coletar.extraction import extract_memories
from coletar.schema.objects import GLOBAL_SCOPE, ExtractionMethod, Provider
from coletar.store.memory import InMemoryStore
from conftest import TENANT

FIXTURES = Path(__file__).parent / "fixtures"
ARCHIVE = FIXTURES / "chatgpt_export.zip"

#: ROADMAP M6: "≥85% extraction precision against a hand-labelled 100-object
#: fixture set".
PRECISION_BAR = 0.85


@pytest.fixture(scope="module")
def labels() -> list[dict]:
    return json.loads((FIXTURES / "chatgpt_export_labels.json").read_text())["labels"]


# --- the tree walk --------------------------------------------------------------


def test_the_active_branch_is_root_to_current_node() -> None:
    mapping = {
        "root": {"id": "root", "parent": None, "children": ["a"], "message": None},
        "a": {"id": "a", "parent": "root", "children": ["b", "c"], "message": {}},
        "b": {"id": "b", "parent": "a", "children": [], "message": {}},
        "c": {"id": "c", "parent": "a", "children": [], "message": {}},
    }
    assert active_branch(mapping, "b") == ["root", "a", "b"]
    assert active_branch(mapping, "c") == ["root", "a", "c"]


def test_a_cycle_in_the_mapping_does_not_hang() -> None:
    """Malformed data, not a reason to spin — the export is user-supplied."""
    mapping = {
        "a": {"id": "a", "parent": "b", "children": [], "message": {}},
        "b": {"id": "b", "parent": "a", "children": [], "message": {}},
    }
    assert len(active_branch(mapping, "a")) <= 2


def test_an_unknown_current_node_yields_nothing() -> None:
    assert active_branch({"a": {"parent": None}}, "missing") == []


@pytest.mark.asyncio
async def test_abandoned_branches_are_never_imported(labels: list[dict]) -> None:
    """The finding this module is built around.

    Every conversation in the fixture carries an edited-away branch saying "Actually
    I want everything in Go from now on" — durable-sounding on purpose. The user
    rejected it. Importing it would put a discarded instruction into the graph at the
    same confidence as one they kept, with nothing downstream able to tell them apart.
    """
    reached = {m.node_id for conv in read_export(ARCHIVE) for m in conv.messages}
    assert reached, "fixture parsed to nothing"
    assert not any(node_id.endswith("-dead") for node_id in reached)

    contents = {m.text for conv in read_export(ARCHIVE) for m in conv.messages}
    assert not any("everything in Go" in text for text in contents)


def test_only_the_users_own_words_are_parsed() -> None:
    conversations = list(read_export(ARCHIVE))
    assert conversations
    for conversation in conversations:
        assert "Understood — here is a reply." not in {m.text for m in conversation.messages}
        # Assistant turns are counted, not silently discarded: "the import found less
        # than you expected" has to be answerable.
        assert conversation.skipped.get("not_user", 0) > 0


def test_non_text_content_is_skipped_rather_than_guessed_at() -> None:
    """`code`, `multimodal_text` and `execution_output` are either not prose or not
    the user's. A parser that guessed would import a pasted stack trace as a
    preference."""
    raw = {
        "id": "c",
        "title": "t",
        "current_node": "n",
        "mapping": {
            "n": {
                "id": "n",
                "parent": None,
                "children": [],
                "message": {
                    "id": "n",
                    "author": {"role": "user"},
                    "content": {"content_type": "code", "parts": ["print(1)"]},
                },
            }
        },
    }
    conversation = parse_conversation(raw)
    assert conversation is not None
    assert conversation.messages == []
    assert conversation.skipped["non_text"] == 1


def test_multimodal_parts_keep_the_typed_half() -> None:
    raw = {
        "id": "c",
        "title": "t",
        "current_node": "n",
        "mapping": {
            "n": {
                "id": "n",
                "parent": None,
                "children": [],
                "message": {
                    "id": "n",
                    "author": {"role": "user"},
                    "content": {
                        "content_type": "text",
                        "parts": [{"asset_pointer": "file-1"}, "I prefer dark mode."],
                    },
                },
            }
        },
    }
    conversation = parse_conversation(raw)
    assert conversation is not None
    assert [m.text for m in conversation.messages] == ["I prefer dark mode."]


# --- the archive ----------------------------------------------------------------


def test_a_non_export_zip_says_so_in_the_users_terms(tmp_path: Path) -> None:
    path = tmp_path / "holiday-photos.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("IMG_0001.jpg", "not json")
    with pytest.raises(ChatGPTExportError, match="is it a ChatGPT export"):
        list(read_export(path))


def test_a_nested_export_folder_is_found(tmp_path: Path) -> None:
    """Exports have shipped both flat and inside a dated top-level folder."""
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("2026-03-01-export/conversations.json", "[]")
    assert list(read_export(path)) == []


def test_a_corrupt_archive_is_a_message_not_a_traceback(tmp_path: Path) -> None:
    path = tmp_path / "broken.zip"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(ChatGPTExportError, match="not a readable ZIP"):
        list(read_export(path))


# --- sharded exports -------------------------------------------------------------
#
# Observed on a real 2026-08 export: 46 `conversations-NNN.json` files, 4,521
# conversations, and no `conversations.json` anywhere in the archive. The parser
# written against the single-file layout raised "is it a ChatGPT export?" on a
# genuine one — and it does so precisely on the largest histories.


def _conversation(node_id: str, text: str) -> dict:
    return {
        "id": f"conv-{node_id}",
        "current_node": node_id,
        "mapping": {
            node_id: {
                "id": node_id,
                "parent": None,
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": [text]},
                },
            }
        },
    }


def test_a_sharded_export_is_read(tmp_path: Path) -> None:
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("conversations-000.json", json.dumps([_conversation("a", "I use vim.")]))
        bundle.writestr("conversations-001.json", json.dumps([_conversation("b", "I use tabs.")]))
    texts = [m.text for c in read_export(path) for m in c.messages]
    assert texts == ["I use vim.", "I use tabs."]


def test_shards_are_read_in_numeric_order(tmp_path: Path) -> None:
    """Import order decides which of two conflicting statements supersedes the
    other, so a reader that shuffled shards would make the graph depend on
    filesystem iteration order."""
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        for n in (2, 0, 10, 1):
            bundle.writestr(
                f"conversations-{n:03d}.json", json.dumps([_conversation(f"n{n}", f"turn {n}")])
            )
    assert [m.text for c in read_export(path) for m in c.messages] == [
        "turn 0",
        "turn 1",
        "turn 2",
        "turn 10",
    ]


def test_an_unpacked_export_directory_is_read(tmp_path: Path) -> None:
    """macOS expands downloads by default, so the folder is what the user has."""
    folder = tmp_path / "chatgpt-export"
    folder.mkdir()
    (folder / "conversations-000.json").write_text(
        json.dumps([_conversation("a", "I ship on Fridays.")])
    )
    (folder / "user.json").write_text("{}")
    assert [m.text for c in read_export(folder) for m in c.messages] == ["I ship on Fridays."]


def test_the_single_file_layout_still_works(tmp_path: Path) -> None:
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("conversations.json", json.dumps([_conversation("a", "I use zsh.")]))
    assert [m.text for c in read_export(path) for m in c.messages] == ["I use zsh."]


def test_a_conversation_marked_do_not_remember_is_not_imported(tmp_path: Path) -> None:
    """The user already declined inside ChatGPT. Reading past that because the
    answer arrived in a file is what the acquisition boundary forbids."""
    raw = _conversation("a", "My salary is 250000 dollars.")
    raw["is_do_not_remember"] = True
    assert parse_conversation(raw) is None


def test_do_not_remember_is_honoured_through_the_archive(tmp_path: Path) -> None:
    kept = _conversation("a", "I prefer Postgres.")
    refused = _conversation("b", "Do not remember this one.")
    refused["is_do_not_remember"] = True
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("conversations-000.json", json.dumps([kept, refused]))
    assert [m.text for c in read_export(path) for m in c.messages] == ["I prefer Postgres."]


def test_a_missing_directory_says_so(tmp_path: Path) -> None:
    with pytest.raises(ChatGPTExportError, match="does not exist"):
        list(read_export(tmp_path / "nope"))


def test_a_folder_without_conversations_names_both_layouts(tmp_path: Path) -> None:
    folder = tmp_path / "downloads"
    folder.mkdir()
    (folder / "user.json").write_text("{}")
    with pytest.raises(ChatGPTExportError, match="conversations-NNN.json"):
        list(read_export(folder))


# --- the measured bar ------------------------------------------------------------


@pytest.mark.asyncio
async def test_extraction_precision_clears_the_bar(labels: list[dict]) -> None:
    by_node = {label["node"]: label for label in labels}
    true_positive = false_positive = false_negative = 0

    for conversation in read_export(ARCHIVE):
        for message in conversation.messages:
            durable = by_node[message.node_id]["durable"]
            extracted = bool(await extract_memories(user_text=message.text, scope=GLOBAL_SCOPE))
            if extracted and durable:
                true_positive += 1
            elif extracted and not durable:
                false_positive += 1
            elif durable:
                false_negative += 1

    extracted_total = true_positive + false_positive
    precision = true_positive / extracted_total if extracted_total else 0.0
    recall = true_positive / (true_positive + false_negative)

    assert precision >= PRECISION_BAR, f"precision {precision:.1%} on {extracted_total}"
    # Reported, not asserted. Recall is low because the extractor is regex over a
    # register it was not tuned for, and pinning a number here would freeze a
    # limitation in place as though it were a target. M6.2 is where it moves.
    assert recall > 0.25, f"recall collapsed to {recall:.1%}"


@pytest.mark.asyncio
async def test_an_import_writes_at_export_confidence_with_provenance() -> None:
    """§3.1: a line recovered from an archive is a weaker signal than a statement
    made to a connector in the moment, and the schema enforces that rather than
    each caller remembering it."""
    store = InMemoryStore()
    report = await import_export(store, TENANT, ARCHIVE)

    assert report.conversations == 10
    assert report.messages == 100
    assert report.created > 0

    objects = await store.list_objects(TENANT, limit=200)
    assert objects
    for obj in objects:
        assert obj.extraction_method is ExtractionMethod.ACCOUNT_EXPORT_PARSE
        assert obj.confidence == 0.60
        assert obj.provenance.provider is Provider.CHATGPT
        # Points at the exact node, so the Inspector can show which line it came from.
        assert obj.provenance.source_object_ids


@pytest.mark.asyncio
async def test_a_restated_preference_corroborates_instead_of_duplicating() -> None:
    """The ingest boundary matters more here than anywhere else. An export is years
    of a user restating the same preference, and without corroboration this would
    create a hundred copies of one fact."""
    store = InMemoryStore()
    first = await import_export(store, TENANT, ARCHIVE)
    second = await import_export(store, TENANT, ARCHIVE)

    assert second.created == 0
    assert second.corroborated == first.created
    assert len(await store.list_objects(TENANT, limit=200)) == first.created
