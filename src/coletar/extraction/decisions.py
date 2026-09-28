"""The two decision stages, phrased as Jev questions (docs/DECISION_EVAL.md).

**Gate.** Live sync asks, per turn, whether anything in it is worth remembering. The
extractor behind it is a paid model call; the gate exists so that ~90% of turns never
reach it. Its dangerous error is the false negative: a rejected turn is never looked
at again, by the extractor or by anyone. So the gate returns a probability and the
caller picks a threshold for *recall*. Precision is the extractor's job.

**Reconcile.** Given a candidate statement and one existing memory it resembles,
decide what the candidate does to the graph. Four labels, and the dangerous one is
`supersedes`, because it retires a memory: a wrong `supersedes` silently deletes
something true, where every other wrong answer leaves a visible mess to clean up.

Both definitions are data. `STAGE_VERSION` hashes them so every evaluation records
exactly which wording it measured — a threshold tuned against one phrasing says
nothing about the next one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Literal, get_args

import httpx

from coletar.extraction.jev import (
    Choice,
    Evaluation,
    JevConfigurationError,
    Noul,
    NoulCriteria,
    evaluate,
)

GATE_QUESTION_NAME = "worth_remembering"
RECONCILE_QUESTION_NAME = "relation"

GATE_QUESTION = Noul(
    instructions=(
        "Does the latest turn tell us something about the user that will still be true"
        " after this conversation ends? The previous turn, if given, is only there to"
        " make the latest one intelligible."
    ),
    criteria=NoulCriteria(
        true=(
            "It reveals something lasting: who the user is, their job or studies, where"
            " they live, people or organisations in their life and how they relate,"
            " standing preferences, recurring habits, long-running projects, goals, or"
            " decisions they have made. A short answer that confirms such a fact counts."
        ),
        false=(
            "It is only about the task in hand: a question, a request, an instruction"
            " for this one reply, pasted text or code, narration of what they are doing"
            " right now, or small talk."
        ),
    ),
)

ReconcileLabel = Literal["new", "duplicate", "supersedes", "contradicts"]
RECONCILE_LABELS: tuple[ReconcileLabel, ...] = get_args(ReconcileLabel)

RECONCILE_QUESTION = Choice(
    instructions=(
        "A candidate statement about the user was just extracted. Compare it with one"
        " memory already stored. What does the candidate do to that memory?"
    ),
    criteria={
        "new": (
            "The candidate says something the existing memory does not, and both can be"
            " true at once. Includes another item in a many-valued category: a second"
            " language they use, another friend, another hobby."
        ),
        "duplicate": (
            "The candidate says the same thing as the existing memory, in the same or"
            " different words, and adds nothing."
        ),
        "supersedes": (
            "The candidate replaces the existing memory: a later change to a one-valued"
            " fact (where they live, their employer, their manager, a current plan), or"
            " the same fact stated more specifically."
        ),
        "contradicts": (
            "Both cannot be true, and the candidate is not clearly a later change: the"
            " dates are equal or missing, the candidate is older, or one looks mistaken."
        ),
    },
)


def _version() -> str:
    definitions = {
        "gate": GATE_QUESTION.model_dump(mode="json"),
        "reconcile": RECONCILE_QUESTION.model_dump(mode="json"),
    }
    blob = json.dumps(definitions, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


STAGE_VERSION = _version()


@dataclass(frozen=True)
class GateDecision:
    probability: float
    evaluation: Evaluation


@dataclass(frozen=True)
class ReconcileDecision:
    label: ReconcileLabel
    confidence: float
    probabilities: dict[str, float]
    evaluation: Evaluation


@dataclass(frozen=True)
class Statement:
    """A memory or candidate as reconcile sees it: the claim and when it was said."""

    content: str
    said_at: date | None = None

    def state(self) -> dict[str, str | None]:
        return {
            "statement": self.content,
            "said_at": self.said_at.isoformat() if self.said_at else None,
        }


async def gate(
    turn: str,
    previous: str | None = None,
    *,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> GateDecision:
    state = {"previous_turn": previous, "latest_turn": turn}
    evaluation = await evaluate(
        state, {GATE_QUESTION_NAME: GATE_QUESTION}, model=model, client=client
    )
    return GateDecision(evaluation.noul(GATE_QUESTION_NAME), evaluation)


async def reconcile(
    candidate: Statement,
    existing: Statement,
    *,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> ReconcileDecision:
    state = {"existing_memory": existing.state(), "candidate": candidate.state()}
    evaluation = await evaluate(
        state, {RECONCILE_QUESTION_NAME: RECONCILE_QUESTION}, model=model, client=client
    )
    answer = evaluation.choice(RECONCILE_QUESTION_NAME)
    if answer.choice not in RECONCILE_LABELS:
        # Jev only returns labels it was given, so this is a contract break.
        raise JevConfigurationError(f"reconcile returned unknown label {answer.choice!r}")
    return ReconcileDecision(
        answer.choice, answer.confidence, dict(answer.probabilities), evaluation
    )
