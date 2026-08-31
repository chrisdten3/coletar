"""Claude conversation export parsing (SCOPE §4.1, §8.1).

Human-initiated, like every acquisition path here: the user opens Settings >
Privacy > Export Data, Anthropic emails a link, and automation begins once the ZIP
is on disk. Nothing in this module touches claude.ai.

**Both providers name their file `conversations.json`, and they are not the same
format.** ChatGPT's is a tree — `mapping`, `current_node`, a fork per edit. Claude's
is a flat list of `chat_messages` per conversation. Before this module existed the
watcher accepted a Claude export as a ChatGPT one, found zero conversations inside
it, and marked the file seen: a silent no-op that looked exactly like success. That
is why `detect` below discriminates on *structure* rather than on the filename, and
why parsing refuses an archive whose shape it does not recognise instead of quietly
returning nothing.

**The export is a manifest and five archives, not one ZIP.** Anthropic emails a
`manifest-*.json` listing single-use download URLs for `conversations`, `memories`,
`projects`, `design_chats` and `light_metadata`. Earlier versions of this module
assumed a single archive holding `conversations.json`, which was wrong in a way worth
recording: it also led to the claim that Claude's memory is not exported. It is.

That matters more than the packaging. `memories` are facts Claude has *already*
extracted, so importing them is near-lossless — against roughly a third of durable
statements recovered by mining conversation prose. And `projects` carry the container
a conversation belonged to, which maps onto `Scope` directly rather than having to be
inferred.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONVERSATIONS = "conversations.json"

#: What the emailed manifest calls each archive. Every one is a separate ZIP behind a
#: single-use URL, which is also why nothing here fetches them: a consumed URL that
#: fails to land is not recoverable, so downloading stays the user's own act.
MANIFEST_CATEGORIES = frozenset(
    {"conversations", "memories", "projects", "design_chats", "light_metadata"}
)

#: Claude marks the person `human`; `assistant` is the model. Only one is evidence
#: about the user, the same discipline as every other acquisition path here.
HUMAN_SENDER = "human"


class ClaudeExportError(Exception):
    """The archive is not a Claude export, phrased for the person who chose it."""


@dataclass(frozen=True)
class ExportedMessage:
    text: str
    message_id: str
    conversation_id: str
    created_at: datetime | None


@dataclass
class ExportedConversation:
    id: str
    title: str
    created_at: datetime | None
    updated_at: datetime | None
    messages: list[ExportedMessage] = field(default_factory=list)
    #: Counted rather than silently dropped: "the import found less than you
    #: expected" has to be answerable.
    skipped: dict[str, int] = field(default_factory=dict)


def _timestamp(raw: Any) -> datetime | None:
    """Claude writes ISO-8601 strings where ChatGPT writes Unix seconds."""
    if isinstance(raw, int | float):
        try:
            return datetime.fromtimestamp(float(raw), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _message_text(message: Any) -> tuple[str | None, str]:
    """The user's own words, or a reason there are none."""
    if not isinstance(message, dict):
        return None, "malformed"
    if message.get("sender") != HUMAN_SENDER:
        return None, "not_user"

    # Newer exports carry a `content` block list beside the flat `text`; older ones
    # only have `text`. Prefer the blocks when present and keep the typed parts, the
    # same rule `claude_code` applies to tool results.
    content = message.get("content")
    if isinstance(content, list):
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(part for part in parts if part).strip()
        if text:
            return text, ""

    text = str(message.get("text") or "").strip()
    return (text, "") if text else (None, "empty")


def parse_conversation(raw: dict[str, Any]) -> ExportedConversation | None:
    messages = raw.get("chat_messages")
    if not isinstance(messages, list):
        return None
    conversation = ExportedConversation(
        id=str(raw.get("uuid") or raw.get("id") or ""),
        title=str(raw.get("name") or raw.get("title") or "").strip() or "(untitled)",
        created_at=_timestamp(raw.get("created_at")),
        updated_at=_timestamp(raw.get("updated_at")),
    )
    for message in messages:
        text, reason = _message_text(message)
        if text is None:
            conversation.skipped[reason] = conversation.skipped.get(reason, 0) + 1
            continue
        conversation.messages.append(
            ExportedMessage(
                text=text,
                message_id=str(message.get("uuid") or message.get("id") or ""),
                conversation_id=conversation.id,
                created_at=_timestamp(message.get("created_at")),
            )
        )
    return conversation


def _conversations_entry(bundle: zipfile.ZipFile) -> str:
    names = bundle.namelist()
    if CONVERSATIONS in names:
        return CONVERSATIONS
    nested = [n for n in names if n.endswith(f"/{CONVERSATIONS}")]
    if nested:
        return min(nested, key=len)
    raise ClaudeExportError(
        f"no {CONVERSATIONS} in this archive — is it a Claude export? "
        f"It holds: {', '.join(sorted(names)[:6]) or '(nothing)'}"
    )


def load_conversations(archive: Path) -> list[Any]:
    """The raw list, shared with `detect` so the shape is read once per question."""
    if not archive.exists():
        raise ClaudeExportError(f"{archive} does not exist")
    try:
        with zipfile.ZipFile(archive) as bundle, bundle.open(
            _conversations_entry(bundle)
        ) as handle:
            payload = json.load(handle)
    except zipfile.BadZipFile as exc:
        raise ClaudeExportError(f"{archive.name} is not a readable ZIP: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ClaudeExportError(
            f"{CONVERSATIONS} in {archive.name} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, list):
        raise ClaudeExportError(f"{CONVERSATIONS} should hold a list of conversations")
    return payload


def read_export(archive: Path) -> Iterator[ExportedConversation]:
    payload = load_conversations(archive)
    recognised = False
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        conversation = parse_conversation(raw)
        if conversation is None:
            continue
        recognised = True
        if conversation.messages:
            yield conversation
    if payload and not recognised:
        # Loud, not silent. An archive full of conversations none of which have
        # `chat_messages` is a format this parser does not understand, and returning
        # nothing would present as "you had no history".
        raise ClaudeExportError(
            f"{archive.name} holds {CONVERSATIONS} but no conversation has "
            "`chat_messages` — this looks like a different provider's export, or a "
            "Claude format this parser has not seen"
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
    """Read a Claude export and offer every human turn to the extractor.

    The same extractor, guards and ingest boundary as the ChatGPT importer, the
    proxy and the browser bridge. A sentence typed into claude.ai is not a different
    kind of statement from one typed into ChatGPT, so it should not get different
    treatment or its own false-positive rate — only the provenance differs, and that
    is what `Provider.CLAUDE` and `ACCOUNT_EXPORT_PARSE` record.
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
                memory.provenance.provider = Provider.CLAUDE
                memory.provenance.confidence = 0.60
                memory.provenance.source_object_ids = [
                    part
                    for part in (message.conversation_id, message.message_id)
                    if part
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
                        provider=Provider.CLAUDE,
                        detail={
                            "surface": "claude-export",
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



@dataclass(frozen=True)
class ManifestEntry:
    category: str
    filename: str
    export_url: str


@dataclass
class ExportBundle:
    """A downloaded Claude export: the manifest, and whichever archives are present.

    Modelled as "some of the five" rather than all of them, because the URLs are
    single-use and a user who lost one to a failed download should still be able to
    import what they did get.
    """

    root: Path
    entries: list[ManifestEntry] = field(default_factory=list)
    present: dict[str, Path] = field(default_factory=dict)

    @property
    def missing(self) -> list[str]:
        return sorted({e.category for e in self.entries} - set(self.present))


def read_manifest(path: Path) -> ExportBundle:
    """Parse the emailed manifest and locate the archives beside it.

    `path` may be the manifest JSON or the directory holding it — people drop the
    whole download folder at a tool as often as they name the file.
    """
    if path.is_dir():
        candidates = sorted(path.glob("manifest-*.json"))
        if not candidates:
            raise ClaudeExportError(
                f"no manifest-*.json in {path} — Anthropic emails one alongside the "
                "archives; point at it or at the folder holding it"
            )
        path = candidates[0]

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaudeExportError(f"{path.name} is not a readable manifest: {exc}") from exc

    files = payload.get("data_files")
    if not isinstance(files, list):
        raise ClaudeExportError(f"{path.name} has no data_files — is it the manifest?")

    bundle = ExportBundle(root=path.parent)
    for row in files:
        if not isinstance(row, dict):
            continue
        entry = ManifestEntry(
            category=str(row.get("category", "")),
            filename=str(row.get("filename", "")),
            export_url=str(row.get("export_url", "")),
        )
        bundle.entries.append(entry)
        archive = bundle.root / entry.filename
        if archive.exists():
            bundle.present[entry.category] = archive
    return bundle


def describe(archive: Path) -> dict[str, Any]:
    """What is actually inside one archive, without assuming a shape.

    The Claude export's per-archive layout is not documented anywhere I could find,
    and guessing it twice would repeat the mistake this module already made once. So
    this reports what is there and lets a human confirm before a parser commits to it.
    """
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        summary: dict[str, Any] = {"files": names[:20], "count": len(names)}
        for name in names:
            if not name.endswith(".json"):
                continue
            with bundle.open(name) as handle:
                try:
                    payload = json.load(handle)
                except json.JSONDecodeError:
                    continue
            if isinstance(payload, list) and payload:
                first = payload[0]
                summary[name] = {
                    "type": "list",
                    "length": len(payload),
                    "keys": sorted(first)[:24] if isinstance(first, dict) else None,
                }
            elif isinstance(payload, dict):
                summary[name] = {"type": "object", "keys": sorted(payload)[:24]}
    return summary
