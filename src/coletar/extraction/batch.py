"""Fan model calls out; keep graph writes in order.

Extraction over an archive is thousands of independent HTTP round trips, and doing
them one at a time is what makes a real import take hours: at ~1.5s per turn, the
17,881-turn export measured in `docs/EXTRACTION_BASELINE.md` is 7.5 hours of almost
entirely waiting.

Only the *calls* are parallel. Writes stay sequential and in input order, because
they are not independent: corroboration depends on whether a fact is already
present, and entity linking threads a shared name→id map through the run. Racing
those would turn "the same fact appeared twice" into two objects instead of one
corroboration, which is the redundancy the whole ingest path exists to avoid.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Sequence
from typing import Any

from coletar.extraction.providers import ExtractionConfigurationError


def turn_hash(text: str) -> str:
    """The checkpoint key for one turn.

    Hashes the text rather than the archive's own ids: ids are stable within one
    export but not guaranteed across a re-export, and hashing the text also means
    the same turn appearing twice is extracted once — which is correct anyway,
    since identical input yields identical output.
    """
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


async def extract_in_order(
    transcripts: Sequence[str],
    *,
    provider: Any,
    scope: Any = None,
    concurrency: int = 8,
) -> AsyncIterator[tuple[int, tuple[list[Any], list[Any]] | BaseException]]:
    """Yield `(index, result-or-exception)` in input order, calling concurrently.

    Exceptions are yielded rather than raised so the caller keeps its existing
    per-turn policy — an unavailable turn is counted, not fatal. The one exception
    is `ExtractionConfigurationError`, which is re-raised immediately: it means the
    run cannot succeed at all, and continuing would repeat a doomed call for every
    remaining turn.
    """
    from coletar.extraction import extract_with_model

    async def one(text: str) -> tuple[list[Any], list[Any]]:
        kwargs: dict[str, Any] = {"transcript": text, "provider": provider}
        if scope is not None:
            kwargs["scope"] = scope
        return await extract_with_model(**kwargs)

    for start in range(0, len(transcripts), concurrency):
        window = transcripts[start : start + concurrency]
        results = await asyncio.gather(*(one(t) for t in window), return_exceptions=True)
        for offset, result in enumerate(results):
            if isinstance(result, ExtractionConfigurationError):
                raise result
            yield start + offset, result
