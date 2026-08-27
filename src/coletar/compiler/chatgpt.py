"""ChatGPT compiler — best-effort Custom GPT package (SCOPE §4, §10 step 4).

M6, and the most constrained leg in the product. OpenAI has no import API, and
their terms cover destination-side automation as well as acquisition — so this
compiler's output is a package the *user* uploads through GPT Builder plus a
memory-entries file they paste in. It must never drive OpenAI's UI.

That constraint is a design boundary, not an MVP shortcut (§8.1, §11).
"""

from __future__ import annotations

from pathlib import Path

from coletar.compiler.base import CompileResult
from coletar.schema.objects import ContextObject


class ChatGPTCompiler:
    destination = "chatgpt"

    async def compile(
        self, objects: list[ContextObject], *, out_dir: Path
    ) -> CompileResult:
        raise NotImplementedError("ChatGPT compiler is M6; see docs/ROADMAP.md")
