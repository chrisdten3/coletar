"""Markdown mirror of the canonical graph (ROADMAP M8).

Basic Memory's best idea, taken deliberately rather than copied. They store plain
Markdown as the source of truth, and it is a genuinely better *ownership* story than
a database row: you can open it, read it, and keep it after the tool is gone.

What it cannot hold is the part that matters here. Supersession chains, an immutable
event log, provenance per object, atomic writes — a directory of files has no way to
make those true, and they are exactly what the audit use case is buying. So the
typed graph stays canonical and this is a **projection** of it.

**One-way by default, and that is the point.** If editing a file silently became
truth, a write would have entered the graph with no event behind it, which is
constraint 5 and the whole reason the log is trustworthy. Edits are supported — see
`pull_edits` — but they are applied *through the ingest boundary*, as real events,
the same way every other surface writes.

**Deterministic on purpose.** Mirroring twice with an unchanged graph produces byte-
identical files, so a vault can live in git and a diff means something changed rather
than that you ran the command again.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coletar.schema.objects import ContextObject, LocalityMode
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: Bounded, like the dashboard's windows: a mirror of a very large graph should be a
#: deliberate act rather than something that quietly takes an hour.
OBJECT_LIMIT = 20_000
EVENT_LIMIT = 20_000

VAULT_README = """# coletar

A Markdown mirror of your context graph. **These files are a projection, not the
source of truth** — the graph is, which is what lets coletar keep provenance,
supersession and an immutable event log that a directory of files cannot.

- `objects/` — one file per object, grouped by type
- `events/` — the append-only Event/Revision Log, by month

Edit freely. Nothing here is read back automatically; run `coletar mirror --pull` to
apply your edits through the same ingest path every other surface writes through, so
they land as real events with real provenance rather than as a silent change.

Supersession is rendered as an Obsidian link, so a correction chain is visible in the
graph view: a fact that replaced another points at what it replaced.
"""


@dataclass
class MirrorReport:
    objects: int = 0
    events: int = 0
    written: list[Path] = field(default_factory=list)
    unchanged: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "objects": self.objects,
            "events": self.events,
            "files_written": len(self.written),
            "unchanged": self.unchanged,
        }


def content_hash(content: str) -> str:
    """Recorded in frontmatter so `pull_edits` can tell an edited file from an
    untouched one without diffing against the store."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _yaml_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    # Quote anything YAML would otherwise reinterpret. Cheap, and avoids a parser.
    if text == "" or text[0] in "&*!|>%@`{[" or ": " in text or text != text.strip():
        return '"' + text.replace('"', '\\"') + '"'
    return text


def frontmatter(obj: ContextObject) -> dict[str, Any]:
    """Everything a file needs to be explainable on its own.

    An object we cannot explain to the user should not exist (§4), and that has to
    survive the trip out of the database — a mirrored file with no provenance would
    be a note, not a record.
    """
    locality = (
        "synced"
        if obj.locality.mode is LocalityMode.SYNCED
        else "local_only:" + ",".join(sorted(str(s) for s in obj.locality.surfaces))
    )
    return {
        "coletar_id": obj.id,
        "type": str(obj.type),
        "kind": str(getattr(obj, "kind", "")) or "null",
        "scope": str(obj.scope),
        "locality": locality,
        "confidence": round(obj.confidence, 4),
        "extraction_method": str(obj.extraction_method),
        "origin": f"{obj.provenance.origin_type}/{obj.provenance.provider}",
        "sensitivity": str(obj.sensitivity),
        "version": obj.version,
        "created": obj.created_at.isoformat(),
        "updated": obj.updated_at.isoformat(),
        "supersedes": obj.supersedes,
        "retired": obj.retired_at.isoformat() if obj.retired_at else None,
        "source_ids": ",".join(obj.provenance.source_object_ids) or None,
        "coletar_hash": content_hash(obj.content),
    }


def render_object(obj: ContextObject) -> str:
    lines = ["---"]
    lines += [f"{key}: {_yaml_value(value)}" for key, value in frontmatter(obj).items()]
    lines += ["---", "", obj.content.strip(), ""]

    if obj.supersedes:
        # An Obsidian link, so a correction chain is navigable in the graph view —
        # the one thing a file tree does better than a database.
        lines += ["", f"Supersedes [[{obj.supersedes}]]", ""]
    if obj.retired_at:
        lines += ["> Retired. Kept for provenance; excluded from retrieval and compile.", ""]
    return "\n".join(lines)


def render_events(events: list[Any]) -> str:
    """The log, oldest first, so appending to a month file is genuinely an append."""
    lines = ["# Event/Revision Log", ""]
    for event in sorted(events, key=lambda e: (e.at, e.id)):
        target = f" [[{event.object_id}]]" if event.object_id else ""
        lines.append(f"- `{event.at.isoformat()}` **{event.type}** by {event.actor}{target}")
    return "\n".join(lines) + "\n"


def _write_if_changed(path: Path, content: str, report: MirrorReport) -> None:
    """Byte-identical output means an unchanged graph produces an empty git diff."""
    if path.exists() and path.read_text(encoding="utf-8") == content:
        report.unchanged += 1
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    report.written.append(path)


async def mirror(store: Store, tenant_id: TenantId, out_dir: Path) -> MirrorReport:
    """Project the graph into a Markdown vault. Reads only; writes no events."""
    report = MirrorReport()
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_if_changed(out_dir / "README.md", VAULT_README, report)

    objects = await store.list_objects(
        tenant_id, include_retired=True, include_superseded=True, limit=OBJECT_LIMIT
    )
    for obj in sorted(objects, key=lambda o: o.id):
        report.objects += 1
        _write_if_changed(
            out_dir / "objects" / str(obj.type) / f"{obj.id}.md", render_object(obj), report
        )

    events = await store.list_events(tenant_id, limit=EVENT_LIMIT)
    report.events = len(events)
    by_month: dict[str, list[Any]] = {}
    for event in events:
        by_month.setdefault(event.at.strftime("%Y-%m"), []).append(event)
    for month, rows in sorted(by_month.items()):
        _write_if_changed(out_dir / "events" / f"{month}.md", render_events(rows), report)

    return report


@dataclass
class PullReport:
    scanned: int = 0
    edited: int = 0
    applied: int = 0
    unknown: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "edited": self.edited,
            "applied": self.applied,
            "unknown": self.unknown,
            "conflicts": self.conflicts,
        }


def parse_mirrored(text: str) -> tuple[dict[str, str], str]:
    """Frontmatter and body from a mirrored file.

    Hand-rolled rather than a YAML dependency: the mirror writes a flat `key: value`
    block and nothing else, so a parser for the general case would be carrying a
    library to read output we control. A dependency has to survive being said out
    loud (AGENTS.md).
    """
    if not text.startswith("---\n"):
        return {}, text.strip()
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, _, value = line.partition(":")
        if not _:
            continue
        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == '"' and cleaned[-1] == '"':
            cleaned = cleaned[1:-1].replace('\\"', '"')
        meta[key.strip()] = cleaned
    return meta, text[end + 4 :].strip()


def _body_content(body: str) -> str:
    """The user's text, minus the affordances the mirror added for Obsidian."""
    lines = [
        line
        for line in body.splitlines()
        if not line.startswith("Supersedes [[") and not line.startswith("> Retired.")
    ]
    return "\n".join(lines).strip()


async def pull_edits(
    store: Store, tenant_id: TenantId, out_dir: Path, *, dry_run: bool = False
) -> PullReport:
    """Apply edits made in the vault, through the ingest boundary.

    This is the half that keeps the mirror honest. A two-way sync that wrote straight
    to the store would put changes in the graph with no event behind them — the one
    thing the substrate must not allow, and the thing that would make the audit trail
    a decoration. So an edited file becomes an ordinary edit: version bumped, event
    appended with before and after, history still answerable.

    Detection is by content hash rather than mtime. Copying a vault between machines
    rewrites every mtime and would otherwise present as "you edited everything".
    """
    from coletar.inspector.review import edit as apply_edit

    report = PullReport()
    objects_dir = out_dir / "objects"
    if not objects_dir.exists():
        return report

    for path in sorted(objects_dir.rglob("*.md")):
        report.scanned += 1
        meta, body = parse_mirrored(path.read_text(encoding="utf-8"))
        object_id = meta.get("coletar_id", "")
        if not object_id:
            report.unknown.append(str(path.relative_to(out_dir)))
            continue

        content = _body_content(body)
        if not content or content_hash(content) == meta.get("coletar_hash"):
            continue
        report.edited += 1

        stored = await store.get_object(tenant_id, object_id)
        if stored is None:
            report.unknown.append(object_id)
            continue
        if content_hash(stored.content) != meta.get("coletar_hash"):
            # The graph moved on since this file was written. Refused rather than
            # resolved: silently picking a winner is how an edit disappears.
            report.conflicts.append(object_id)
            continue

        if not dry_run:
            await apply_edit(store, tenant_id, object_id, content=content)
        report.applied += 1

    return report
