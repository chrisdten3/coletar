"""Score Jev on a labelled decision set: the Phase 0 go/no-go (docs/DECISION_EVAL.md).

    uv run python scripts/eval_jev.py gate      ~/coletar-decisions/gate.json
    uv run python scripts/eval_jev.py reconcile ~/coletar-decisions/reconcile.json

    # No private set yet? The committed fixtures work as a first read:
    uv run python scripts/eval_jev.py gate      tests/fixtures/extraction_set.json
    uv run python scripts/eval_jev.py reconcile tests/fixtures/reconcile_seed.json

Needs `COLETAR_JEV_API_KEY` (or `TYPESAFE_API_KEY`). Sends each item to TypeSafe.
Writes per-item results next to the set (`*.results.<stage>.json`) so every miss can
be read, not just counted.

Exit criteria, from the plan:
  gate       recall >= 95% at the chosen threshold
  reconcile  false-supersede rate < 2% at the chosen confidence floor

The gate threshold is picked on the same set it is scored on, which flatters it.
Confirm on a second set before shipping a threshold.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from coletar.config import get_settings
from coletar.extraction.decisions import RECONCILE_QUESTION_NAME, STAGE_VERSION, gate, reconcile
from coletar.extraction.evaluation import (
    GateItem,
    ReconcileItem,
    ReconcileScore,
    gate_report,
    load_gate_set,
    load_reconcile_set,
    percentile,
    reconcile_report,
)
from coletar.extraction.jev import Evaluation, JevConfigurationError, JevUnavailable

ATTEMPTS = 3


async def _with_retry[T](call: Callable[[], Awaitable[T]]) -> T | None:
    """Transient failures get three tries; a configuration error stops the run."""
    for attempt in range(ATTEMPTS):
        try:
            return await call()
        except JevUnavailable as exc:
            if attempt == ATTEMPTS - 1:
                print(f"  unavailable after {ATTEMPTS} tries: {exc}", file=sys.stderr)
                return None
            await asyncio.sleep(2 ** (attempt + 1))
    return None


def _accounting(evaluations: list[Evaluation], price: float | None) -> dict[str, object]:
    latencies = [e.latency_ms for e in evaluations]
    tokens = sum(e.usage.input_tokens or 0 for e in evaluations)
    models = sorted({e.model for e in evaluations})
    out: dict[str, object] = {
        "calls": len(evaluations),
        "models": models,
        "latency_ms": {
            "p50": round(percentile(latencies, 50)),
            "p95": round(percentile(latencies, 95)),
            "max": round(max(latencies, default=0)),
        },
        "input_tokens": tokens,
        "input_tokens_per_call": round(tokens / len(evaluations)) if evaluations else 0,
    }
    if price is not None:
        out["cost_usd"] = round(tokens / 1_000_000 * price, 6)
    return out


def _pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


async def run_gate(args: argparse.Namespace, client: httpx.AsyncClient) -> dict[str, object]:
    items = [i for i in load_gate_set(args.set).items if i.durable is not None]
    limit = asyncio.Semaphore(args.concurrency)

    async def one(item: GateItem) -> tuple[GateItem, Evaluation, float] | None:
        async with limit:
            decision = await _with_retry(lambda: gate(item.turn, item.previous, client=client))
        return None if decision is None else (item, decision.evaluation, decision.probability)

    results = [r for r in await asyncio.gather(*(one(i) for i in items)) if r is not None]
    report = gate_report([(item, p) for item, _, p in results], target_recall=args.target_recall)

    print(
        f"\n  gate · {report.n} labelled turns · {report.positives} durable · stage {STAGE_VERSION}"
    )
    print("  threshold  recall  precision  pass-rate")
    for row in report.rows:
        print(
            f"     {row.threshold:.2f}   {_pct(row.recall)}   {_pct(row.precision)}"
            f"    {_pct(row.pass_rate)}"
        )
    if report.at_target:
        t = report.at_target
        verdict = "PASS" if t.recall >= args.target_recall else "FAIL"
        print(
            f"\n  highest threshold keeping {_pct(args.target_recall).strip()} of durable turns:"
            f" {t.threshold:.3f} → recall {_pct(t.recall)},"
            f" pass rate {_pct(t.pass_rate)}  [{verdict}]"
        )
        print(f"  missed at {t.threshold:.3f}: {', '.join(t.missed) or 'none'}")

    return {
        "summary": {
            "n": report.n,
            "positives": report.positives,
            "rows": [row.__dict__ for row in report.rows],
            "at_target": report.at_target.__dict__ if report.at_target else None,
        },
        "items": [
            {
                "id": item.id,
                "durable": item.durable,
                "p": p,
                "model": e.model,
                "request_id": e.request_id,
            }
            for item, e, p in results
        ],
        "unavailable": len(items) - len(results),
        "accounting": _accounting([e for _, e, _ in results], args.price_per_mtok),
    }


async def run_reconcile(args: argparse.Namespace, client: httpx.AsyncClient) -> dict[str, object]:
    items = [i for i in load_reconcile_set(args.set).items if i.label is not None]
    limit = asyncio.Semaphore(args.concurrency)

    async def one(item: ReconcileItem) -> tuple[ReconcileScore, Evaluation] | None:
        async with limit:
            decision = await _with_retry(
                lambda: reconcile(
                    item.candidate.statement(), item.existing.statement(), client=client
                )
            )
        if decision is None:
            return None
        return ReconcileScore(item, decision.label, decision.confidence), decision.evaluation

    results = [r for r in await asyncio.gather(*(one(i) for i in items)) if r is not None]
    report = reconcile_report([s for s, _ in results])

    print(
        f"\n  reconcile · {report.n} labelled pairs · accuracy {_pct(report.accuracy)}"
        f" · stage {STAGE_VERSION}"
    )
    labels = list(report.confusion)
    print("  truth \\ said   " + "".join(f"{label[:11]:>12}" for label in labels))
    for truth in labels:
        print(f"  {truth:<14} " + "".join(f"{report.confusion[truth][p]:>12}" for p in labels))
    print("\n  floor  false-supersede  supersede-recall  flagged  accuracy(unflagged)")
    for row in report.rows:
        verdict = "PASS" if row.false_supersede_rate < args.max_false_supersede else "FAIL"
        print(
            f"   {row.floor:.2f}       {_pct(row.false_supersede_rate)}"
            f"          {_pct(row.supersede_recall)}"
            f"     {_pct(row.flag_rate)}       {_pct(row.accuracy_unflagged)}   [{verdict}]"
        )
    wrong = report.rows[0].false_supersedes
    print(f"\n  false supersedes with no floor: {', '.join(wrong) or 'none'}")

    return {
        "summary": {
            "n": report.n,
            "accuracy": report.accuracy,
            "confusion": report.confusion,
            "per_label": report.per_label,
            "rows": [row.__dict__ for row in report.rows],
        },
        "items": [
            {
                "id": s.item.id,
                "label": s.item.label,
                "predicted": s.predicted,
                "confidence": s.confidence,
                "probabilities": e.choice(RECONCILE_QUESTION_NAME).probabilities,
                "model": e.model,
                "request_id": e.request_id,
            }
            for s, e in results
        ],
        "unavailable": len(items) - len(results),
        "accounting": _accounting([e for _, e in results], args.price_per_mtok),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("stage", choices=["gate", "reconcile"])
    parser.add_argument("set", type=Path)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--target-recall", type=float, default=0.95)
    parser.add_argument("--max-false-supersede", type=float, default=0.02)
    parser.add_argument(
        "--price-per-mtok",
        type=float,
        default=None,
        help="Input price per million tokens, for a cost line. No default: check pricing.",
    )
    args = parser.parse_args()

    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.jev_timeout_seconds) as client:
        try:
            if args.stage == "gate":
                result = await run_gate(args, client)
            else:
                result = await run_reconcile(args, client)
        except JevConfigurationError as exc:
            sys.exit(f"  stopped: {exc}")

    accounting = result["accounting"]
    print(f"\n  jev: {json.dumps(accounting)}")
    if result["unavailable"]:
        print(
            f"  {result['unavailable']} items unavailable after retries; excluded from the scores"
        )

    result["meta"] = {
        "stage": args.stage,
        "set": str(args.set),
        "stage_version": STAGE_VERSION,
        "measured_at": datetime.now(UTC).isoformat(),
    }
    out = args.set.with_name(f"{args.set.stem}.results.{args.stage}.json")
    if not args.set.resolve().is_relative_to(Path.cwd().resolve() / "tests"):
        out.write_text(json.dumps(result, indent=2, default=str))
        print(f"  per-item results: {out}")
    else:
        # Results for committed fixtures stay out of the repo tree.
        alt = Path.home() / "coletar-decisions" / out.name
        alt.parent.mkdir(parents=True, exist_ok=True)
        alt.write_text(json.dumps(result, indent=2, default=str))
        print(f"  per-item results: {alt}")


if __name__ == "__main__":
    asyncio.run(main())
