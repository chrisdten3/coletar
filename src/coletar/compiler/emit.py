"""Shared emission machinery for every provider compiler.

Two compilers made the duplication visible: scope planning, eligibility and
provenance rendering are properties of *the graph*, not of any destination, so they
belong in one place. What stays per-compiler is the only thing that is genuinely
destination-specific — which containers exist, and what lands in each.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from coletar.compiler.continuity import (
    Fidelity,
    MigrationManifest,
    WithheldEntry,
)
from coletar.schema.objects import ContextObject, Provider, Scope, ScopeType


def compile_eligible(objects: list[ContextObject]) -> list[ContextObject]:
    """The set a compile is *asked* to move.

    Retired and superseded objects are filtered here rather than counted as losses:
    they are not failures of the destination, they are objects the graph already
    decided no longer state the current truth. Everything surviving this filter is
    in the Continuity Score denominator, so an object the compiler cannot place is
    counted against coverage instead of quietly dropped.
    """
    superseded = {o.supersedes for o in objects if o.supersedes}
    return [o for o in objects if o.is_active and o.id not in superseded]


def partition_by_locality(
    objects: list[ContextObject], destination: Provider
) -> tuple[list[ContextObject], list[WithheldEntry]]:
    """Split off what the user said may not go to this destination.

    A compile is the one operation that physically hands context to another company,
    which makes it the *last* place locality should be treated as an internal-caller
    exemption. Live Sync reads are something the user watches happen; a compile is a
    transfer they cannot take back, so it filters harder, not softer.

    Withheld objects never reach `source_object_count`, so they are not scored as
    coverage the destination lost. The user did not ask for them to move.
    """
    compilable: list[ContextObject] = []
    withheld: list[WithheldEntry] = []
    for obj in objects:
        if obj.locality.visible_to(destination):
            compilable.append(obj)
        else:
            withheld.append(
                WithheldEntry(
                    source_id=obj.id,
                    source_type=str(obj.type),
                    allowed_surfaces=sorted(str(s) for s in obj.locality.surfaces),
                )
            )
    return compilable, withheld


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-")


@dataclass
class ScopePlan:
    """One destination container: a scope, its objects, and inherited globals."""

    scope: Scope
    name: str
    owned: list[ContextObject] = field(default_factory=list)
    inherited: list[ContextObject] = field(default_factory=list)

    @property
    def is_global(self) -> bool:
        return self.scope.type is ScopeType.GLOBAL


def plan_scopes(
    eligible: list[ContextObject],
    *,
    name_for: Callable[[Scope], str],
    inherit_globals: bool = True,
) -> list[ScopePlan]:
    """Fan the graph out, one container per scope.

    Global first, so a project container inherits a stable prefix. Globals are
    inherited *into* project containers because global means "applies everywhere";
    project objects are never lifted out, because that is the leak that
    `scope_preservation` exists to catch.
    """
    globals_ = [o for o in eligible if o.scope.type is ScopeType.GLOBAL]
    global_scope = Scope(type=ScopeType.GLOBAL)
    plans = [ScopePlan(scope=global_scope, name=name_for(global_scope), owned=globals_)]

    by_project: dict[str, list[ContextObject]] = {}
    for obj in eligible:
        if obj.scope.type is ScopeType.PROJECT and obj.scope.id:
            by_project.setdefault(obj.scope.id, []).append(obj)

    for project_id in sorted(by_project):
        scope = Scope(type=ScopeType.PROJECT, id=project_id)
        plans.append(
            ScopePlan(
                scope=scope,
                name=name_for(scope),
                owned=by_project[project_id],
                inherited=globals_ if inherit_globals else [],
            )
        )
    return plans


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def render_provenance(objects: list[ContextObject]) -> str:
    """§4: an object we cannot explain to the user should not exist — including
    after it has left for another product."""
    lines = [
        "# Provenance",
        "",
        "Every compiled object, where it came from, and how sure coletar is.",
        "",
        "| id | type | scope | confidence | origin | extraction | supersedes |",
        "|---|---|---|---|---|---|---|",
    ]
    for obj in sorted(objects, key=lambda o: o.id):
        lines.append(
            f"| `{obj.id}` | {obj.type} | {obj.scope} | {obj.confidence:.2f} | "
            f"{obj.provenance.origin_type}/{obj.provenance.provider} | "
            f"{obj.extraction_method} | {obj.supersedes or '—'} |"
        )
    return "\n".join(lines) + "\n"


def render_manifest(
    manifest: MigrationManifest,
    plans: list[ScopePlan],
    *,
    container_label: str,
    native_note: str,
    reconstructed_note: str,
) -> str:
    summary = manifest.summary()
    lines = [
        f"# Migration Manifest — {manifest.destination}",
        "",
        f"Compiled {manifest.compiled_at.isoformat()}",
        "",
        f"- **native** {summary['native']} — {native_note}",
        f"- **reconstructed** {summary['reconstructed']} — {reconstructed_note}",
        f"- **unsupported** {summary['unsupported']} — no safe destination representation",
        "",
        f"## {container_label}",
        "",
    ]
    for plan in plans:
        lines.append(
            f"- `{plan.name}` — scope {plan.scope}, {len(plan.owned)} owned, "
            f"{len(plan.inherited)} inherited from global"
        )
    if manifest.withheld:
        lines += [
            "",
            "## Withheld",
            "",
            "Kept out of this compile because you marked them local to another surface. "
            "Listed so you can confirm they stayed put.",
            "",
            "| id | type | allowed on |",
            "|---|---|---|",
        ]
        for held in manifest.withheld:
            lines.append(
                f"| `{held.source_id}` | {held.source_type} | "
                f"{', '.join(held.allowed_surfaces)} |"
            )
    lines += ["", "## Objects", "", "| id | fidelity | destination | note |", "|---|---|---|---|"]
    for entry in manifest.entries:
        lines.append(
            f"| `{entry.source_id}` | {entry.fidelity} | "
            f"{entry.destination_id or '—'} | {entry.note or ''} |"
        )
    return "\n".join(lines) + "\n"


def destination_id(fidelity: Fidelity, native: str, reconstructed: str) -> str | None:
    if fidelity is Fidelity.UNSUPPORTED:
        return None
    return native if fidelity is Fidelity.NATIVE else reconstructed
