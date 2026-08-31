"""Claude Code acquisition (SCOPE §4.1, M3.4).

Claude Code writes every session to `~/.claude/projects/<encoded-cwd>/<id>.jsonl` as
it works. Those files are a documented, user-facing artifact on the user's own disk —
reading them is closer to reading your own shell history than to scraping, and it is
the same thing OpenAI's Import feature does. It is explicitly *not* the prohibited
act, which is automating a provider's site or reading an authenticated page (§8.1).

This is the largest **tier-1** surface in §4.1's table: capture here is guaranteed
rather than discretionary, because the transcript is written whether or not any model
chose to call a tool. No connector, no instruction snippet, no approval prompt.

**The finding that shapes this module.** In a real session file, 930 records had
`type: "user"` — and 873 of them were `tool_result`. Ninety-four percent of what
looks like "the user said" is tool output being fed back: file contents, command
output, stack traces. A parser that trusted the record type would fill the graph with
grep results. So the discriminator here is the *content shape*, never the record
type, and `tool_result` blocks are dropped before anything reaches the extractor.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coletar.schema.objects import GLOBAL_SCOPE, Scope, ScopeType

#: Where Claude Code keeps its per-project session transcripts.
_PROJECTS = "projects"


def default_root() -> Path:
    return Path.home() / ".claude"


@dataclass(frozen=True)
class Turn:
    """One thing the user actually typed."""

    text: str
    cwd: str | None
    session_id: str | None
    uuid: str | None
    timestamp: str | None
    source: Path

    @property
    def provenance_ids(self) -> list[str]:
        """What this came from, so the Context Inspector can point at the line."""
        return [part for part in (self.session_id, self.uuid) if part]


def _human_text(message: dict[str, Any]) -> str:
    """The user's own words, and nothing else.

    A string content block is someone typing. A list may hold `text` blocks (also
    typing) alongside `tool_result` and `image` blocks, which are not. Only `text`
    survives -- this is the single most important line in the module, because
    without it the overwhelming majority of what gets extracted is tool output.
    """
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block.get("text", "").strip()
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def iter_turns(path: Path, *, skip_lines: int = 0) -> Iterator[Turn]:
    """Human turns from one session file, oldest first.

    `skip_lines` makes re-reading cheap and idempotent: a watcher records how far it
    got and resumes there. Deduplication would catch a re-read anyway, but relying on
    it would mean re-embedding every turn in a 3,000-line transcript on every tick.
    """
    with path.open(encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle):
            if index < skip_lines or not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # a partially-written line; the next pass will catch it
            if record.get("type") != "user":
                continue
            # Sidechain turns are a sub-agent's, not the user's.
            if record.get("isSidechain"):
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            text = _human_text(message)
            if not text:
                continue
            yield Turn(
                text=text,
                cwd=record.get("cwd"),
                session_id=record.get("sessionId"),
                uuid=record.get("uuid"),
                timestamp=record.get("timestamp"),
                source=path,
            )


def session_files(root: Path | None = None) -> list[Path]:
    projects = (root or default_root()) / _PROJECTS
    if not projects.is_dir():
        return []
    return sorted(projects.glob("*/*.jsonl"))


def scope_for(cwd: str | None, *, project_scopes: bool = True) -> Scope:
    """A Claude Code session belongs to a directory, and a directory is a project.

    That mapping is free and worth taking: it means a fact stated while working on one
    repository does not surface as global context in an unrelated one. Scope ids are
    derived from the directory name rather than the full path, so the graph does not
    record where on someone's disk their work happens to live.
    """
    if not project_scopes or not cwd:
        return GLOBAL_SCOPE
    name = Path(cwd).name.strip().lower()
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name).strip("-")
    if len(cleaned) < 2:
        return GLOBAL_SCOPE
    return Scope(type=ScopeType.PROJECT, id=f"proj_{cleaned}"[:64])


@dataclass
class ImportReport:
    files: int = 0
    turns: int = 0
    stored: int = 0
    corroborated: int = 0
    #: file path -> lines consumed, for an incremental next run.
    offsets: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "turns": self.turns,
            "stored": self.stored,
            "corroborated": self.corroborated,
        }


def _load_offsets(state_path: Path) -> dict[str, int]:
    if not state_path.exists():
        return {}
    try:
        loaded: dict[str, int] = json.loads(state_path.read_text())
        return loaded
    except ValueError:
        return {}


async def import_sessions(
    store: Any,
    tenant_id: Any,
    *,
    root: Path | None = None,
    state_path: Path | None = None,
    project_scopes: bool = True,
    rescan: bool = False,
) -> ImportReport:
    """Read every session transcript and offer each human turn to the extractor.

    Deliberately the same extractor and the same ingest path the proxy and the browser
    bridge use. A turn typed into Claude Code is not a different kind of statement
    from one typed into a local model, so it should not get different treatment, a
    different false-positive rate, or its own bugs.

    Incremental by default: only lines not seen on a previous run are read.
    `rescan` re-reads everything, which is what to do after the extractor improves —
    deduplication makes that safe, just slower.
    """
    from coletar.extraction import extract_memories
    from coletar.ingest import remember
    from coletar.schema.events import Actor, Event, EventType
    from coletar.schema.objects import ExtractionMethod, Provider

    root = root or default_root()
    state_path = state_path or (root / "coletar-import-state.json")
    offsets = {} if rescan else _load_offsets(state_path)
    report = ImportReport(offsets=dict(offsets))

    for path in session_files(root):
        key = str(path)
        seen = 0 if rescan else offsets.get(key, 0)
        consumed = seen
        touched = False

        for turn in iter_turns(path, skip_lines=seen):
            consumed += 1
            touched = True
            report.turns += 1
            scope = scope_for(turn.cwd, project_scopes=project_scopes)
            for memory in await extract_memories(user_text=turn.text, scope=scope):
                # The user typed this themselves, in their own words, in their own
                # editor. That is the highest-confidence tier there is (§3.1).
                memory.extraction_method = ExtractionMethod.EXPLICIT_STATEMENT
                memory.provenance.provider = Provider.CLAUDE
                memory.provenance.source_object_ids = turn.provenance_ids
                result = await remember(
                    store,
                    tenant_id,
                    memory,
                    event=Event(
                        type=EventType.CONNECTOR_WRITE,
                        object_id=memory.id,
                        actor=Actor.USER,
                        provider=Provider.CLAUDE,
                        detail={
                            "surface": "claude-code",
                            "scope": str(scope),
                            # The transcript stays where it is. This points at it, so
                            # the Inspector can show where a memory came from without
                            # the graph holding a second copy of the conversation.
                            "session_file": path.name,
                            "session_id": turn.session_id,
                        },
                    ),
                    caller_surface=Provider.CLAUDE,
                )
                if result.created:
                    report.stored += 1
                else:
                    report.corroborated += 1

        # Count every line, not just the human ones, or the next run rereads the gaps.
        report.offsets[key] = sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
        if touched:
            report.files += 1

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(report.offsets, indent=2))
    return report
