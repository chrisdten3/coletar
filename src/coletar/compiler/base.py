"""Provider Compiler (SCOPE §3 True Migration, §5).

A compiler is directional and point-in-time: canonical objects in, the destination's
*actual native containers* out. The test of a compiler is §3's promise — after it
runs, the user can disconnect from coletar entirely and the destination product
still works on its own. A zip of markdown does not pass that test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from coletar.compiler.continuity import ContinuityScore, MigrationManifest
from coletar.schema.objects import ContextObject


@dataclass
class CompileResult:
    manifest: MigrationManifest
    score: ContinuityScore
    artifacts: list[Path]
    instructions: str  # what the user must do by hand, stated plainly


class Compiler(Protocol):
    destination: str

    async def compile(
        self, objects: list[ContextObject], *, out_dir: Path
    ) -> CompileResult: ...
