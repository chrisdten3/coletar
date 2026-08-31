"""ChatGPT export parsing (SCOPE §4.1, §10 step 4, ROADMAP M6).

Acquisition here is **human-initiated**: the user clicks their own export button in
ChatGPT's settings, OpenAI emails them a ZIP, and automation begins once that file
has landed on their disk. Nothing in this module touches a provider's site, reads an
authenticated page, or reuses a session cookie (§8.1, §11). It reads a file the user
already has.

**The finding that shapes this module.** `conversations.json` does not hold a list of
messages — it holds a *tree*. Every edit and every regeneration forks the thread, and
all branches ship in the export. Walking `mapping` and taking every message would
import the answers the user threw away alongside the ones they kept, at equal
confidence, with nothing downstream able to tell them apart. So this walks the active
branch only: from `current_node` back through `parent` to the root, which is exactly
the thread the user was left looking at.

That is the same discipline as `claude_code.py`, where 94% of records marked as user
input were tool output. In both cases the record's *position* lies about its meaning,
and the fix is to trust structure rather than labels.

Precision over recall throughout (AGENTS.md): a wrong memory costs the user a
deletion and some trust, a missing one costs almost nothing. Everything uncertain is
dropped rather than guessed at.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: The only file in the archive this parser reads. `user.json`, `message_feedback`
#: and `model_comparisons` carry nothing about what the user believes.
CONVERSATIONS = "conversations.json"

#: Only plain text is the user speaking in their own words. `code`, `multimodal_text`,
#: `execution_output`, `thoughts` and friends are either not prose or not theirs, and
#: a parser that guessed at them would import a pasted stack trace as a preference.
TEXT_CONTENT_TYPE = "text"

#: Author roles that are the user. `system` is the product talking; `assistant` and
#: `tool` are the model. Only one of these is evidence about the person.
USER_ROLE = "user"

#: A defensive bound on how deep a `parent` chain is followed. A malformed or
#: adversarial export could contain a cycle, and a tree walk is not worth hanging on.
MAX_BRANCH_DEPTH = 20_000


class ChatGPTExportError(Exception):
    """The archive is not a ChatGPT export, phrased for the person who chose it."""


@dataclass(frozen=True)
class ExportedMessage:
    """One thing the user actually typed, on the branch they kept."""

    text: str
    node_id: str
    conversation_id: str
    created_at: datetime | None


@dataclass
class ExportedConversation:
    id: str
    title: str
    created_at: datetime | None
    updated_at: datetime | None
    #: Active branch only, oldest first.
    messages: list[ExportedMessage] = field(default_factory=list)
    #: Nodes on the active branch that were skipped, and why. Reported rather than
    #: silently dropped, because "the import found less than you expected" is a
    #: question the Context Inspector has to be able to answer.
    skipped: dict[str, int] = field(default_factory=dict)


def _timestamp(raw: Any) -> datetime | None:
    """Export times are Unix seconds, and are routinely null on placeholder nodes."""
    if not isinstance(raw, int | float):
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def active_branch(mapping: dict[str, Any], current_node: str | None) -> list[str]:
    """Node ids from root to `current_node`, following `parent` only.

    Returning the branch rather than filtering in place is deliberate: which messages
    were *considered* and discarded is real information, and a later slice that wants
    to count them should not have to re-derive the walk.
    """
    if not current_node or current_node not in mapping:
        return []
    chain: list[str] = []
    seen: set[str] = set()
    node_id: str | None = current_node
    while node_id and node_id in mapping and len(chain) < MAX_BRANCH_DEPTH:
        if node_id in seen:  # a cycle is malformed data, not a reason to spin
            break
        seen.add(node_id)
        chain.append(node_id)
        parent = mapping[node_id].get("parent")
        node_id = parent if isinstance(parent, str) else None
    chain.reverse()
    return chain


def _message_text(message: Any) -> tuple[str | None, str]:
    """The user's own words, or a reason there are none."""
    if not isinstance(message, dict):
        return None, "placeholder"
    author = message.get("author")
    role = author.get("role") if isinstance(author, dict) else None
    if role != USER_ROLE:
        return None, "not_user"
    content = message.get("content")
    if not isinstance(content, dict):
        return None, "no_content"
    if content.get("content_type") != TEXT_CONTENT_TYPE:
        return None, "non_text"
    parts = content.get("parts")
    if not isinstance(parts, list):
        return None, "no_parts"
    # Multimodal turns interleave strings with image dicts; keep the typed half.
    text = "\n".join(part for part in parts if isinstance(part, str)).strip()
    if not text:
        return None, "empty"
    return text, ""


def parse_conversation(raw: dict[str, Any]) -> ExportedConversation | None:
    mapping = raw.get("mapping")
    if not isinstance(mapping, dict):
        return None
    conversation = ExportedConversation(
        id=str(raw.get("id") or raw.get("conversation_id") or ""),
        title=str(raw.get("title") or "").strip() or "(untitled)",
        created_at=_timestamp(raw.get("create_time")),
        updated_at=_timestamp(raw.get("update_time")),
    )
    for node_id in active_branch(mapping, raw.get("current_node")):
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            continue
        text, reason = _message_text(node.get("message"))
        if text is None:
            conversation.skipped[reason] = conversation.skipped.get(reason, 0) + 1
            continue
        message = node["message"]
        conversation.messages.append(
            ExportedMessage(
                text=text,
                node_id=node_id,
                conversation_id=conversation.id,
                created_at=_timestamp(message.get("create_time")),
            )
        )
    return conversation


def read_export(archive: Path) -> Iterator[ExportedConversation]:
    """Conversations from a ChatGPT export ZIP, active branch only.

    The archive is read through `ZipFile.open` and never extracted to disk, so a
    crafted entry name cannot escape anywhere — the classic zip-slip does not apply
    to a reader that never writes.
    """
    if not archive.exists():
        raise ChatGPTExportError(f"{archive} does not exist")
    try:
        with zipfile.ZipFile(archive) as bundle:
            name = _conversations_entry(bundle)
            with bundle.open(name) as handle:
                payload = json.load(handle)
    except zipfile.BadZipFile as exc:
        raise ChatGPTExportError(f"{archive.name} is not a readable ZIP: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ChatGPTExportError(
            f"{CONVERSATIONS} in {archive.name} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(payload, list):
        raise ChatGPTExportError(f"{CONVERSATIONS} should hold a list of conversations")
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        conversation = parse_conversation(raw)
        if conversation is not None and conversation.messages:
            yield conversation


def _conversations_entry(bundle: zipfile.ZipFile) -> str:
    """Exports have shipped both flat and inside a dated top-level folder."""
    names = bundle.namelist()
    if CONVERSATIONS in names:
        return CONVERSATIONS
    nested = [n for n in names if n.endswith(f"/{CONVERSATIONS}")]
    if nested:
        return min(nested, key=len)
    raise ChatGPTExportError(
        f"no {CONVERSATIONS} in this archive — is it a ChatGPT export? "
        f"It holds: {', '.join(sorted(names)[:6]) or '(nothing)'}"
    )


@dataclass
class ImportReport:
    conversations: int = 0
    messages: int = 0
    extracted: int = 0
    created: int = 0
    corroborated: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "conversations": self.conversations,
            "messages": self.messages,
            "extracted": self.extracted,
            "created": self.created,
            "corroborated": self.corroborated,
            "skipped": dict(sorted(self.skipped.items())),
        }


async def import_export(
    store: Any,
    tenant_id: Any,
    archive: Path,
    *,
    scope: Any = None,
) -> ImportReport:
    """Read an export and offer every human turn to the extractor.

    Deliberately the same extractor and the same ingest path the proxy, the browser
    bridge and the Claude Code importer use. A sentence typed into ChatGPT is not a
    different kind of statement from one typed into a local model, so it should not
    get different treatment, a different false-positive rate, or its own bugs.

    Two things *are* different, and both are properties of the source rather than of
    the sentence. Confidence is `ACCOUNT_EXPORT_PARSE` (0.60 by §3.1's table, against
    0.95 for something typed live) because a line recovered from an archive is a
    weaker signal than a statement made to a connector in the moment. And the ingest
    boundary matters far more here than anywhere else: an export is thousands of
    turns in which the user restated the same preference across years of chats, so
    without corroboration this would create a hundred copies of one fact — the exact
    redundancy M4.3 measured as absent from a curated corpus.
    """
    from coletar.extraction import extract_memories
    from coletar.ingest import remember
    from coletar.schema.events import Actor, Event, EventType
    from coletar.schema.objects import GLOBAL_SCOPE, ExtractionMethod, Provider

    scope = GLOBAL_SCOPE if scope is None else scope
    report = ImportReport()

    for conversation in read_export(archive):
        report.conversations += 1
        for reason, count in conversation.skipped.items():
            report.skipped[reason] = report.skipped.get(reason, 0) + count

        for message in conversation.messages:
            report.messages += 1
            for memory in await extract_memories(user_text=message.text, scope=scope):
                memory.extraction_method = ExtractionMethod.ACCOUNT_EXPORT_PARSE
                memory.confidence = 0.60
                memory.provenance.provider = Provider.CHATGPT
                memory.provenance.confidence = 0.60
                # Points back at the exact node, so the Context Inspector can show a
                # user which line of their own export a memory came from.
                memory.provenance.source_object_ids = [
                    part for part in (message.conversation_id, message.node_id) if part
                ]
                report.extracted += 1
                result = await remember(
                    store,
                    tenant_id,
                    memory,
                    event=Event(
                        type=EventType.CONNECTOR_WRITE,
                        object_id=memory.id,
                        actor=Actor.MIGRATION,
                        provider=Provider.CHATGPT,
                        detail={
                            "surface": "chatgpt-export",
                            "archive": archive.name,
                            "conversation": conversation.title,
                            "scope": str(scope),
                        },
                    ),
                )
                if result.created:
                    report.created += 1
                else:
                    report.corroborated += 1
    return report
