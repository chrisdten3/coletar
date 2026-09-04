"""Compare extraction providers through the complete production guard path.

This calls paid APIs when an Anthropic or OpenAI spec is selected. A spec is
`provider:model`; examples:

    uv run python scripts/bench_extraction.py \
      openai:gpt-5.6-luna openai:gpt-5.6-terra \
      anthropic:claude-sonnet-5

`BENCH_SET` is `live`, `transient`, or a path to a private labelled set. `BENCH_RUNS`
defaults to 1; use 3 for a decision run. Output separates raw model judgement from
the guarded result users would actually receive.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from coletar.extraction.extractor import _grounded
from coletar.extraction.prompt import EXTRACTION_SYSTEM
from coletar.extraction.proposal import Proposal
from coletar.extraction.providers import (
    ExtractionProviderName,
    ExtractionUnavailable,
    propose,
)

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
SETS = {"live": "extraction_set.json", "transient": "transient_set.json"}
PROVIDERS = frozenset({"ollama", "anthropic", "openai"})


@dataclass
class Score:
    provider: str
    model: str
    run: int
    stage: str
    turns: int
    writes: int
    true_positives: int
    false_positives: int
    false_negatives: int
    wrong_kind: int
    unavailable: int
    seconds: float
    fixture_sha256: str
    prompt_sha256: str
    measured_at: str

    @property
    def precision(self) -> float:
        return self.true_positives / self.writes if self.writes else 0.0

    @property
    def recall(self) -> float:
        total = self.true_positives + self.false_negatives
        return self.true_positives / total if total else 0.0

    def record(self) -> dict[str, object]:
        return {
            **asdict(self),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
        }


def _memory_result(proposal: Proposal | None) -> tuple[bool, str | None]:
    if proposal is None or not proposal.memories:
        return False, None
    return True, proposal.memories[0].kind.value


def _add(score: Score, *, turn: dict[str, object], proposal: Proposal | None) -> None:
    got, kind = _memory_result(proposal)
    durable = bool(turn.get("durable"))
    if got:
        score.writes += 1
    if durable and got:
        score.true_positives += 1
        expected = turn.get("kind")
        if expected and kind != expected:
            score.wrong_kind += 1
    elif durable:
        score.false_negatives += 1
    elif got:
        score.false_positives += 1


async def score(
    provider: ExtractionProviderName,
    model: str,
    turns: list[dict[str, object]],
    *,
    run: int,
    fixture_hash: str,
) -> tuple[Score, Score]:
    common = {
        "provider": provider,
        "model": model,
        "run": run,
        "turns": len(turns),
        "writes": 0,
        "true_positives": 0,
        "false_positives": 0,
        "false_negatives": 0,
        "wrong_kind": 0,
        "unavailable": 0,
        "seconds": 0.0,
        "fixture_sha256": fixture_hash,
        "prompt_sha256": hashlib.sha256(EXTRACTION_SYSTEM.encode()).hexdigest(),
        "measured_at": datetime.now(UTC).isoformat(),
    }
    raw = Score(stage="proposal", **common)
    guarded = Score(stage="guarded", **common)
    started = time.perf_counter()
    for turn in turns:
        transcript = str(turn["user"])
        try:
            result = await propose(transcript=transcript, provider=provider, model=model)
        except ExtractionUnavailable:
            raw.unavailable += 1
            guarded.unavailable += 1
            continue
        _add(raw, turn=turn, proposal=result)
        _add(guarded, turn=turn, proposal=_grounded(result, transcript) if result else None)
    elapsed = round(time.perf_counter() - started, 3)
    raw.seconds = guarded.seconds = elapsed
    return raw, guarded


def _spec(value: str) -> tuple[ExtractionProviderName, str]:
    provider, separator, model = value.partition(":")
    if not separator or provider not in PROVIDERS or not model:
        raise SystemExit(f"invalid {value!r}; expected provider:model for {sorted(PROVIDERS)}")
    return provider, model  # type: ignore[return-value]


async def main() -> None:
    specs = sys.argv[1:] or [
        "openai:gpt-5.6-luna",
        "openai:gpt-5.6-terra",
        "openai:gpt-5.6-sol",
        "anthropic:claude-haiku-4-5",
        "anthropic:claude-sonnet-5",
    ]
    which = os.environ.get("BENCH_SET", "live")
    source = FIXTURES / SETS[which] if which in SETS else Path(which).expanduser()
    if not source.exists():
        raise SystemExit(f"BENCH_SET is not a known set or file: {which}")
    payload = source.read_bytes()
    decoded = json.loads(payload)
    turns = decoded if isinstance(decoded, list) else decoded["turns"]
    runs = int(os.environ.get("BENCH_RUNS", "1"))
    fixture_hash = hashlib.sha256(payload).hexdigest()

    print(f"set={source} turns={len(turns)} runs={runs}")
    for raw_spec in specs:
        provider, model = _spec(raw_spec)
        for run in range(1, runs + 1):
            raw, guarded = await score(
                provider, model, turns, run=run, fixture_hash=fixture_hash
            )
            for card in (raw, guarded):
                print(json.dumps(card.record(), sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
