"""Importing a claude.ai conversation export.

The fixture holds the same 100 labelled turns as the ChatGPT one, re-shaped into
Claude's format. That is deliberate: both importers share an extractor, so a
precision difference between them would be a *parser* bug rather than a corpus
difference — and only a shared corpus can show that.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from coletar.acquisition.claude_export import (
    ClaudeExportError,
    import_export,
    parse_conversation,
    read_export,
)
from coletar.acquisition.watcher import detect
from coletar.extraction import extract_memories
from coletar.schema.objects import GLOBAL_SCOPE, ExtractionMethod, Provider
from coletar.store.memory import InMemoryStore
from conftest import TENANT

FIXTURES = Path(__file__).parent / "fixtures"
ARCHIVE = FIXTURES / "claude_export.zip"
CHATGPT_ARCHIVE = FIXTURES / "chatgpt_export.zip"

PRECISION_BAR = 0.85


@pytest.fixture(scope="module")
def labels() -> list[dict]:
    return json.loads((FIXTURES / "claude_export_labels.json").read_text())["labels"]


# --- the two formats are told apart ----------------------------------------------


def test_the_two_providers_are_told_apart_by_structure() -> None:
    """Both ship `conversations.json`; only the shape distinguishes them."""
    assert detect(ARCHIVE) == "claude"
    assert detect(CHATGPT_ARCHIVE) == "chatgpt"


def test_a_chatgpt_export_is_refused_loudly_rather_than_read_as_empty() -> None:
    """The bug in the other direction, and the one that matters more.

    Returning nothing would present to the user as "you had no history". An importer
    that cannot recognise its input has to say so.
    """
    with pytest.raises(ClaudeExportError, match="chat_messages"):
        list(read_export(CHATGPT_ARCHIVE))


def test_a_non_export_zip_says_so_in_the_users_terms(tmp_path: Path) -> None:
    path = tmp_path / "tax-returns.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("2025.pdf", "not json")
    with pytest.raises(ClaudeExportError, match="is it a Claude export"):
        list(read_export(path))


# --- only the user's own words ----------------------------------------------------


def test_only_human_turns_are_parsed() -> None:
    conversations = list(read_export(ARCHIVE))
    assert conversations
    for conversation in conversations:
        assert "Understood — here is a reply." not in {m.text for m in conversation.messages}
        assert conversation.skipped.get("not_user", 0) > 0


def test_content_blocks_are_preferred_over_the_flat_text_field() -> None:
    """Newer exports carry typed blocks beside `text`; keep the typed parts, the same
    rule `claude_code` applies to tool results."""
    raw = {
        "uuid": "c",
        "name": "t",
        "chat_messages": [
            {
                "uuid": "m",
                "sender": "human",
                "text": "",
                "content": [
                    {"type": "image", "source": {}},
                    {"type": "text", "text": "I prefer dark mode."},
                ],
            }
        ],
    }
    conversation = parse_conversation(raw)
    assert conversation is not None
    assert [m.text for m in conversation.messages] == ["I prefer dark mode."]


def test_iso_timestamps_are_read() -> None:
    """Claude writes ISO-8601 where ChatGPT writes Unix seconds."""
    conversation = next(iter(read_export(ARCHIVE)))
    assert conversation.created_at is not None
    assert conversation.created_at.year == 2026


# --- the measured bar, on the shared corpus ---------------------------------------


@pytest.mark.asyncio
async def test_extraction_precision_matches_the_chatgpt_importer(labels: list[dict]) -> None:
    by_message = {label["message"]: label for label in labels}
    true_positive = false_positive = false_negative = 0

    for conversation in read_export(ARCHIVE):
        for message in conversation.messages:
            durable = by_message[message.message_id]["durable"]
            extracted = bool(await extract_memories(user_text=message.text, scope=GLOBAL_SCOPE))
            if extracted and durable:
                true_positive += 1
            elif extracted and not durable:
                false_positive += 1
            elif durable:
                false_negative += 1

    total = true_positive + false_positive
    precision = true_positive / total if total else 0.0
    recall = true_positive / (true_positive + false_negative)

    assert precision >= PRECISION_BAR, f"precision {precision:.1%} on {total}"
    # Same content through the same extractor: the numbers should match the ChatGPT
    # importer's, and a divergence means one of the two parsers is losing turns.
    assert total == 11, total
    assert recall > 0.25


@pytest.mark.asyncio
async def test_an_import_records_claude_as_the_provider() -> None:
    store = InMemoryStore()
    report = await import_export(store, TENANT, ARCHIVE)

    assert report.conversations == 10
    assert report.messages == 100
    assert report.created > 0

    objects = await store.list_objects(TENANT, limit=200)
    assert objects
    for obj in objects:
        assert obj.provenance.provider is Provider.CLAUDE
        assert obj.extraction_method is ExtractionMethod.ACCOUNT_EXPORT_PARSE
        assert obj.confidence == 0.60
        assert obj.provenance.source_object_ids


@pytest.mark.asyncio
async def test_the_same_statement_from_both_providers_corroborates() -> None:
    """The round trip, as one assertion.

    A preference the user stated in ChatGPT and again in Claude is one fact with two
    sources, not two facts. Without the shared ingest boundary an importer per
    provider would double the graph every time someone switched tools.
    """
    from coletar.acquisition.chatgpt_export import import_export as import_chatgpt

    store = InMemoryStore()
    first = await import_chatgpt(store, TENANT, CHATGPT_ARCHIVE)
    second = await import_export(store, TENANT, ARCHIVE)

    assert first.created > 0
    assert second.created == 0
    assert second.corroborated == first.created
    assert len(await store.list_objects(TENANT, limit=200)) == first.created
