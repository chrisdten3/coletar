"""Normalization / Extraction Layer (SCOPE §5).

Per §5 this is where most of the real engineering risk lives: turning a raw export
or a live turn into correctly-typed, correctly-scoped, correctly-confidence-scored
objects is a much harder problem than "chunk and embed."

What exists today is a deliberately conservative heuristic baseline — it only fires
on unambiguous first-person statements, and it tags everything
`explicit_statement`. It exists so the local proxy loop is closed end to end from
day one, not because a regex is the answer. The LLM-assisted path below is M2.
"""

from __future__ import annotations

import re

from coletar.schema.objects import (
    GLOBAL_SCOPE,
    ExtractionMethod,
    Memory,
    MemoryKind,
    OriginType,
    Provider,
    Scope,
)

# Only unambiguous first-person declarations. Precision over recall: a wrong memory
# is worse than a missing one, because the user has to find and delete it.
_PATTERNS: list[tuple[re.Pattern[str], MemoryKind]] = [
    (re.compile(r"\bremember that\s+(?P<body>.+)", re.I), MemoryKind.FACT),
    (re.compile(r"\bmy name is\s+(?P<body>.+)", re.I), MemoryKind.FACT),
    (re.compile(r"\bi (?:prefer|like|always use)\s+(?P<body>.+)", re.I), MemoryKind.PREFERENCE),
    (re.compile(r"\bi (?:never|don't|do not) (?:use|want)\s+(?P<body>.+)", re.I),
     MemoryKind.PREFERENCE),
    (re.compile(r"\bi'?m (?:working on|building)\s+(?P<body>.+)", re.I), MemoryKind.GOAL),
    (re.compile(r"\b(?:from now on|going forward),?\s+(?P<body>.+)", re.I),
     MemoryKind.INSTRUCTION),
    (re.compile(r"\b(?:actually|no,)\s*(?:it'?s|i meant)\s+(?P<body>.+)", re.I),
     MemoryKind.CORRECTION),
]

_MAX_LEN = 400


def _clean(body: str) -> str:
    return re.split(r"[.!?\n]", body.strip(), maxsplit=1)[0].strip(" ,;:")[:_MAX_LEN]


async def extract_memories(
    *,
    user_text: str,
    assistant_text: str = "",
    scope: Scope = GLOBAL_SCOPE,
    provider: Provider = Provider.LOCAL,
) -> list[Memory]:
    """Extract durable memories from one conversational turn.

    Only the user's own words are mined. The assistant's reply is passed in for
    future use by the LLM-assisted path (which needs the exchange to resolve
    referents) but is never itself treated as a source of fact — a model's
    statements about the user are inference, not testimony.
    """
    del assistant_text  # reserved for the M2 LLM-assisted path

    found: list[Memory] = []
    seen: set[str] = set()
    for pattern, kind in _PATTERNS:
        for match in pattern.finditer(user_text):
            body = _clean(match.group("body"))
            if len(body) < 4 or body.lower() in seen:
                continue
            seen.add(body.lower())
            found.append(
                Memory.from_write(
                    content=body,
                    kind=kind,
                    scope=scope,
                    provider=provider,
                    extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
                    origin_type=OriginType.USER,
                )
            )
    return found


async def extract_with_model(*, transcript: str, scope: Scope = GLOBAL_SCOPE) -> list[Memory]:
    """LLM-assisted typed extraction with confidence scoring and dedup/merge.

    M2. Note the cost risk named in §11: this runs on every export at consumer
    scale, so it must be modelled before the consumer tier is priced. The local
    wedge should run it against the user's own local model, where inference is free.
    """
    raise NotImplementedError("LLM-assisted extraction is M2; see docs/ROADMAP.md")
