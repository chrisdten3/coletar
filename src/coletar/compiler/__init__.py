"""Provider Compiler — canonical objects into a destination's native containers."""

from coletar.compiler.base import Compiler, CompileResult
from coletar.compiler.claude import ClaudeCompiler
from coletar.compiler.continuity import (
    WEIGHTS,
    ContinuityScore,
    Fidelity,
    ManifestEntry,
    MigrationManifest,
    score,
)
from coletar.compiler.emit import compile_eligible
from coletar.compiler.local import LocalModelCompiler

__all__ = [
    "WEIGHTS",
    "ClaudeCompiler",
    "CompileResult",
    "Compiler",
    "ContinuityScore",
    "Fidelity",
    "LocalModelCompiler",
    "ManifestEntry",
    "MigrationManifest",
    "compile_eligible",
    "score",
]
