"""Claude compiler — native Claude Project (SCOPE §4, §10 step 3).

M3. The best surface by a wide margin: Anthropic ships an official memory
import/export format, so this compiler targets an existing spec instead of
reverse-engineering one, and it is the first real True Migration proof point.

Shape: canonical objects -> a Project system prompt (instructions, preferences,
corrections) + project knowledge files (facts, artifacts, decisions), emitted in
Anthropic's import format for the user to import themselves.
"""

from __future__ import annotations

from pathlib import Path

from coletar.compiler.base import CompileResult
from coletar.schema.objects import ContextObject


class ClaudeCompiler:
    destination = "claude"

    async def compile(
        self, objects: list[ContextObject], *, out_dir: Path
    ) -> CompileResult:
        raise NotImplementedError("Claude compiler is M3; see docs/ROADMAP.md")
