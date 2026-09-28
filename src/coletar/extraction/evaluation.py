"""Labelled sets and metrics for the decision stages (docs/DECISION_EVAL.md).

Kept in the package rather than a script because the numbers decide whether Jev
ships in two pipeline stages, and arithmetic that gates a decision should be tested
like any other code.

The metrics are chosen by which error is expensive, not by what is conventional:

* **Gate**: recall first. A missed turn is a memory nobody ever sees; a false pass
  costs one extractor call. So the report answers "what is the highest threshold that
  still keeps 95% of durable turns, and how many turns pass at it" — the second
  number is the cost of the first.
* **Reconcile**: the false-supersede rate first. `supersedes` retires a memory, so a
  wrong one quietly removes something true. Low-confidence answers can be routed to
  "keep both and flag", which is always safe, so the report sweeps a confidence floor
  and shows what each floor costs in flags.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from coletar.extraction.decisions import RECONCILE_LABELS, ReconcileLabel, Statement

# -- labelled sets -----------------------------------------------------------------


class GateItem(BaseModel):
    """One turn, with the turn before it for context.

    `durable` is the human label and the only one metrics read. `draft` is what the
    pre-labelling model proposed; it is kept so the set records how often a person
    overruled it, which is the measure of how far the draft can be trusted.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    turn: str
    previous: str | None = None
    durable: bool | None = None
    draft: bool | None = None
    draft_reason: str | None = None
    #: Sampling stratum and inverse-probability weight. Durable turns are rare, so
    #: the set over-samples turns the draft thought durable; the weight lets the
    #: pass rate be reported for the population rather than for the sample.
    stratum: str | None = None
    weight: float = 1.0
    source: str | None = None


class StatementRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    said_at: date | None = None

    def statement(self) -> Statement:
        return Statement(self.content, self.said_at)


class ReconcileItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    candidate: StatementRecord
    existing: StatementRecord
    label: ReconcileLabel | None = None
    draft: ReconcileLabel | None = None
    draft_reason: str | None = None
    #: Why this pair is in the set, for hand-written hard cases.
    note: str | None = None
    source: str | None = None


class GateSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = "gate"
    items: list[GateItem] = Field(default_factory=list)


class ReconcileSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = "reconcile"
    items: list[ReconcileItem] = Field(default_factory=list)


def load_gate_set(path: Path) -> GateSet:
    """A gate set, or one of the older extraction fixtures read as one.

    `tests/fixtures/extraction_set.json` and `transient_set.json` already carry a
    human `durable` label per turn, so they are a usable gate set with no previous
    turn — enough for a first measurement before a new set is labelled.
    """
    raw = json.loads(path.read_text())
    if isinstance(raw, dict) and "turns" in raw and "items" not in raw:
        return GateSet(
            items=[
                GateItem(id=t["id"], turn=t["user"], durable=bool(t["durable"]), source=path.name)
                for t in raw["turns"]
            ]
        )
    return GateSet.model_validate(raw)


def load_reconcile_set(path: Path) -> ReconcileSet:
    raw = json.loads(path.read_text())
    return ReconcileSet.model_validate(raw)


def save_set(labelled: GateSet | ReconcileSet, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(labelled.model_dump_json(indent=2, exclude_none=True) + "\n")


# -- gate metrics ------------------------------------------------------------------


@dataclass(frozen=True)
class GateRow:
    threshold: float
    recall: float
    precision: float
    #: Share of all turns (weighted to the population) that would reach the
    #: extractor. This is the gate's cost number.
    pass_rate: float
    missed: tuple[str, ...]


@dataclass(frozen=True)
class GateReport:
    n: int
    positives: int
    rows: list[GateRow]
    #: Highest threshold keeping at least `target_recall` of durable turns, and the
    #: row at it. None when the set has no durable turns.
    target_recall: float
    at_target: GateRow | None


def _gate_row(scored: Sequence[tuple[GateItem, float]], threshold: float) -> GateRow:
    tp = fp = 0
    passed_weight = total_weight = 0.0
    missed: list[str] = []
    for item, p in scored:
        passed = p >= threshold
        total_weight += item.weight
        if passed:
            passed_weight += item.weight
        if item.durable:
            if passed:
                tp += 1
            else:
                missed.append(item.id)
        elif passed:
            fp += 1
    positives = tp + len(missed)
    return GateRow(
        threshold=threshold,
        recall=tp / positives if positives else 0.0,
        precision=tp / (tp + fp) if tp + fp else 0.0,
        pass_rate=passed_weight / total_weight if total_weight else 0.0,
        missed=tuple(missed),
    )


def gate_report(
    scored: Sequence[tuple[GateItem, float]],
    *,
    thresholds: Sequence[float] = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9),
    target_recall: float = 0.95,
) -> GateReport:
    """`scored` pairs each labelled item with Jev's probability of yes."""
    labelled = [(item, p) for item, p in scored if item.durable is not None]
    rows = [_gate_row(labelled, t) for t in thresholds]

    positive_scores = sorted((p for item, p in labelled if item.durable), reverse=True)
    at_target = None
    if positive_scores:
        # Keeping k positives needs a threshold at or below the k-th highest positive
        # score; the highest such threshold is that score itself.
        k = math.ceil(target_recall * len(positive_scores))
        at_target = _gate_row(labelled, positive_scores[k - 1])
    return GateReport(
        n=len(labelled),
        positives=len(positive_scores),
        rows=rows,
        target_recall=target_recall,
        at_target=at_target,
    )


# -- reconcile metrics -------------------------------------------------------------


@dataclass(frozen=True)
class ReconcileRow:
    """What the stage does if answers below `floor` are routed to keep-both-and-flag."""

    floor: float
    #: Wrong `supersedes` over all pairs whose truth is not `supersedes`: the
    #: probability that a memory which should have stayed gets retired.
    false_supersede_rate: float
    #: Real updates caught: `supersedes` answered, at or above the floor, when true.
    supersede_recall: float
    flag_rate: float
    accuracy_unflagged: float
    false_supersedes: tuple[str, ...]


@dataclass
class ReconcileReport:
    n: int
    accuracy: float
    confusion: dict[str, dict[str, int]]
    per_label: dict[str, dict[str, float]]
    rows: list[ReconcileRow] = field(default_factory=list)


@dataclass(frozen=True)
class ReconcileScore:
    item: ReconcileItem
    predicted: ReconcileLabel
    confidence: float


def _reconcile_row(scored: Sequence[ReconcileScore], floor: float) -> ReconcileRow:
    not_supersede = sum(1 for s in scored if s.item.label != "supersedes")
    supersede = len(scored) - not_supersede
    false_supersedes: list[str] = []
    caught = flagged = correct_unflagged = 0
    for s in scored:
        if s.confidence < floor:
            flagged += 1
            continue
        if s.predicted == s.item.label:
            correct_unflagged += 1
        if s.predicted == "supersedes":
            if s.item.label == "supersedes":
                caught += 1
            else:
                false_supersedes.append(s.item.id)
    unflagged = len(scored) - flagged
    return ReconcileRow(
        floor=floor,
        false_supersede_rate=len(false_supersedes) / not_supersede if not_supersede else 0.0,
        supersede_recall=caught / supersede if supersede else 0.0,
        flag_rate=flagged / len(scored) if scored else 0.0,
        accuracy_unflagged=correct_unflagged / unflagged if unflagged else 0.0,
        false_supersedes=tuple(false_supersedes),
    )


def reconcile_report(
    scored: Sequence[ReconcileScore],
    *,
    floors: Sequence[float] = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9),
) -> ReconcileReport:
    labelled = [s for s in scored if s.item.label is not None]
    confusion: dict[str, dict[str, int]] = {
        truth: dict.fromkeys(RECONCILE_LABELS, 0) for truth in RECONCILE_LABELS
    }
    for s in labelled:
        assert s.item.label is not None
        confusion[s.item.label][s.predicted] += 1

    per_label: dict[str, dict[str, float]] = {}
    for label in RECONCILE_LABELS:
        tp = confusion[label][label]
        predicted = sum(confusion[t][label] for t in RECONCILE_LABELS)
        actual = sum(confusion[label].values())
        per_label[label] = {
            "precision": tp / predicted if predicted else 0.0,
            "recall": tp / actual if actual else 0.0,
            "support": float(actual),
        }
    correct = sum(confusion[label][label] for label in RECONCILE_LABELS)
    return ReconcileReport(
        n=len(labelled),
        accuracy=correct / len(labelled) if labelled else 0.0,
        confusion=confusion,
        per_label=per_label,
        rows=[_reconcile_row(labelled, f) for f in floors],
    )


# -- shared ------------------------------------------------------------------------


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile; enough for latency over a few hundred calls."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def label_agreement(items: Sequence[GateItem] | Sequence[ReconcileItem]) -> dict[str, Any]:
    """How often the human kept the draft label: a measure of the drafter, not Jev."""
    pairs: Counter[str] = Counter()
    for item in items:
        human = item.durable if isinstance(item, GateItem) else item.label
        if human is None or item.draft is None:
            continue
        pairs["kept" if human == item.draft else "overruled"] += 1
    total = sum(pairs.values())
    return {"reviewed": total, "overruled": pairs["overruled"], "kept": pairs["kept"]}
