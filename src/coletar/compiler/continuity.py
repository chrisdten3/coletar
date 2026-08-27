"""Migration Manifest and Continuity Score (SCOPE §7).

The score has to survive scrutiny or it is a badge, not a differentiator — so the
weighting is a public constant in this module, the four terms are computed from
manifest facts rather than estimated, and `explain()` renders the arithmetic for
any user who asks. If you change WEIGHTS, change docs/CONTINUITY_SCORE.md too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Fidelity(StrEnum):
    """Appendix D manifest categories, carried forward from v0.1."""

    NATIVE = "native"           # landed in a real container of the right type
    RECONSTRUCTED = "reconstructed"  # preserved, but flattened into prose
    UNSUPPORTED = "unsupported"      # no destination representation exists


#: Published weighting. Coverage and fidelity dominate: a migration that drops
#: objects or flattens all of them has failed regardless of how fresh it is.
WEIGHTS: dict[str, float] = {
    "object_coverage": 0.40,
    "fidelity": 0.30,
    "scope_preservation": 0.20,
    "staleness": 0.10,
}

#: A compile is fully fresh for a day, then decays to zero over 30 days.
STALENESS_FLOOR_DAYS = 30.0


@dataclass
class ManifestEntry:
    source_id: str
    source_type: str
    fidelity: Fidelity
    destination_type: str | None = None
    destination_id: str | None = None
    scope_preserved: bool = True
    note: str | None = None


@dataclass
class MigrationManifest:
    """The honest record of one compile: what landed natively, what was
    reconstructed, and what the destination simply cannot hold."""

    destination: str
    entries: list[ManifestEntry] = field(default_factory=list)
    compiled_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def add(self, entry: ManifestEntry) -> None:
        self.entries.append(entry)

    @property
    def total(self) -> int:
        return len(self.entries)

    def count(self, fidelity: Fidelity) -> int:
        return sum(1 for e in self.entries if e.fidelity is fidelity)

    def summary(self) -> dict[str, int]:
        return {
            "total": self.total,
            "native": self.count(Fidelity.NATIVE),
            "reconstructed": self.count(Fidelity.RECONSTRUCTED),
            "unsupported": self.count(Fidelity.UNSUPPORTED),
        }


@dataclass(frozen=True)
class ContinuityScore:
    object_coverage: float
    fidelity: float
    scope_preservation: float
    staleness: float
    total: float

    def explain(self) -> str:
        rows = [
            f"  {name:<20} {getattr(self, name):.3f} x {WEIGHTS[name]:.2f} "
            f"= {getattr(self, name) * WEIGHTS[name]:.3f}"
            for name in WEIGHTS
        ]
        return "\n".join(["Continuity Score", *rows, f"  {'total':<20} {self.total:.3f}"])


def _staleness_term(compiled_at: datetime, *, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    days = max(0.0, (now - compiled_at).total_seconds() / 86_400.0)
    if days <= 1.0:
        return 1.0
    return max(0.0, 1.0 - (days - 1.0) / STALENESS_FLOOR_DAYS)


def score(
    manifest: MigrationManifest,
    *,
    source_object_count: int,
    project_scoped_source_count: int | None = None,
    now: datetime | None = None,
) -> ContinuityScore:
    """Compute the score from manifest facts.

    `source_object_count` is the number of objects the compile was *asked* to move,
    so objects that never reached the manifest at all are counted as lost rather
    than quietly excluded from the denominator.
    """
    if source_object_count <= 0:
        return ContinuityScore(0.0, 0.0, 0.0, 0.0, 0.0)

    mapped = sum(1 for e in manifest.entries if e.fidelity is not Fidelity.UNSUPPORTED)
    object_coverage = mapped / source_object_count

    fidelity = (manifest.count(Fidelity.NATIVE) / manifest.total) if manifest.total else 0.0

    scoped = [e for e in manifest.entries if e.scope_preserved is not None]
    denominator = (
        project_scoped_source_count
        if project_scoped_source_count is not None
        else len(scoped)
    )
    if denominator:
        scope_preservation = sum(1 for e in scoped if e.scope_preserved) / denominator
    else:
        scope_preservation = 1.0  # nothing was project-scoped; nothing could be lost

    staleness = _staleness_term(manifest.compiled_at, now=now)

    terms = {
        "object_coverage": min(1.0, object_coverage),
        "fidelity": min(1.0, fidelity),
        "scope_preservation": min(1.0, scope_preservation),
        "staleness": staleness,
    }
    total = sum(terms[name] * WEIGHTS[name] for name in WEIGHTS)
    return ContinuityScore(total=round(total, 4), **{k: round(v, 4) for k, v in terms.items()})
