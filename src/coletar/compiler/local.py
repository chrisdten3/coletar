"""Local-model compiler — a native profile file (SCOPE §4, §10 step 1).

The one compiler with no third-party constraint at all, which makes it the right
place to get manifest and Continuity Score semantics correct before touching
anyone's ToS. Emits an Ollama Modelfile SYSTEM block plus a knowledge directory.

M1 — first compiler to build.
"""

from __future__ import annotations

from pathlib import Path

from coletar.compiler.base import CompileResult
from coletar.schema.objects import ContextObject


class LocalModelCompiler:
    destination = "local"

    async def compile(
        self, objects: list[ContextObject], *, out_dir: Path
    ) -> CompileResult:
        raise NotImplementedError("Local compiler is M1; see docs/ROADMAP.md")
