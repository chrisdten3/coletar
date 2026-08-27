"""Provider Compiler — canonical objects into a destination's native containers."""

from coletar.compiler.base import Compiler, CompileResult
from coletar.compiler.continuity import (
    WEIGHTS,
    ContinuityScore,
    Fidelity,
    ManifestEntry,
    MigrationManifest,
    score,
)

__all__ = [
    "WEIGHTS",
    "CompileResult",
    "Compiler",
    "ContinuityScore",
    "Fidelity",
    "ManifestEntry",
    "MigrationManifest",
    "score",
]
