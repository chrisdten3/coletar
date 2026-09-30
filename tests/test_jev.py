"""The Jev client, the two decision stages built on it, and the Phase 0 metrics.

No test here reaches TypeSafe: the transport is an `httpx.MockTransport`, and the
request it records is the contract. The live check is `scripts/eval_jev.py`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from coletar.config import get_settings
from coletar.extraction.decisions import (
    GATE_QUESTION_NAME,
    RECONCILE_LABELS,
    RECONCILE_QUESTION_NAME,
    Statement,
    gate,
    reconcile,
)
from coletar.extraction.evaluation import (
    GateItem,
    ReconcileItem,
    ReconcileScore,
    StatementRecord,
    gate_report,
    load_gate_set,
    load_reconcile_set,
    percentile,
    reconcile_report,
)
from coletar.extraction.jev import (
    Choice,
    JevConfigurationError,
    JevUnavailable,
    Noul,
    NoulCriteria,
    evaluate,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def jev_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("COLETAR_JEV_API_KEY", "test-key")
    monkeypatch.setenv("COLETAR_JEV_BASE_URL", "https://jev.test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(
    handler: Callable[[httpx.Request], httpx.Response], seen: list[httpx.Request] | None = None
) -> httpx.AsyncClient:
    def record(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(record))


def _ok(answers: dict[str, Any], **extra: Any) -> httpx.Response:
    body = {"model": "jev-2026-09", "usage": {"input_tokens": 90, "output_tokens": 3}}
    body["answers"] = answers
    body.update(extra)
    return httpx.Response(200, json=body, headers={"x-typesafe-request-id": "req_1"})


# -- client ------------------------------------------------------------------------


async def test_request_matches_the_systemone_contract() -> None:
    seen: list[httpx.Request] = []
    client = _client(lambda _: _ok({"q": {"type": "noul", "noul": 0.8}}), seen)
    question = Noul(instructions="Is it?", criteria=NoulCriteria(true="yes it is"))

    result = await evaluate({"turn": "hi"}, {"q": question}, client=client)

    request = seen[0]
    assert str(request.url) == "https://jev.test/v1/systemone"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert json.loads(request.content) == {
        "state": {"turn": "hi"},
        "model": "jev-latest",
        # An unset criterion is omitted rather than sent as null.
        "questions": {
            "q": {"type": "noul", "instructions": "Is it?", "criteria": {"true": "yes it is"}}
        },
    }
    assert result.noul("q") == 0.8
    assert result.model == "jev-2026-09"
    assert result.usage.input_tokens == 90
    assert result.request_id == "req_1"


async def test_choice_answers_parse() -> None:
    answer = {
        "type": "choice",
        "choice": "b",
        "confidence": 0.7,
        "probabilities": {"a": 0.3, "b": 0.7},
    }
    client = _client(lambda _: _ok({"c": answer}))
    result = await evaluate("state", {"c": Choice(criteria={"a": "A", "b": "B"})}, client=client)
    assert result.choice("c").choice == "b"
    assert result.choice("c").probabilities == {"a": 0.3, "b": 0.7}


async def test_unknown_answer_types_are_skipped_not_fatal() -> None:
    answers = {"q": {"type": "noul", "noul": 0.1}, "later": {"type": "rank", "rank": [1]}}
    result = await evaluate("s", {"q": Noul()}, client=_client(lambda _: _ok(answers)))
    assert set(result.answers) == {"q"}


async def test_an_unanswered_question_is_a_contract_break() -> None:
    client = _client(lambda _: _ok({}))
    with pytest.raises(JevConfigurationError, match="unanswered"):
        await evaluate("s", {"q": Noul()}, client=client)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_statuses_are_retryable(status: int) -> None:
    client = _client(lambda _: httpx.Response(status, text="busy"))
    with pytest.raises(JevUnavailable):
        await evaluate("s", {"q": Noul()}, client=client)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_permanent_statuses_stop(status: int) -> None:
    client = _client(lambda _: httpx.Response(status, text="no"))
    with pytest.raises(JevConfigurationError):
        await evaluate("s", {"q": Noul()}, client=client)


async def test_timeouts_are_retryable() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(JevUnavailable, match="timeout"):
        await evaluate("s", {"q": Noul()}, client=_client(slow))


async def test_a_malformed_answer_stops() -> None:
    client = _client(lambda _: _ok({"q": {"type": "noul", "noul": "high"}}))
    with pytest.raises(JevConfigurationError, match="schema"):
        await evaluate("s", {"q": Noul()}, client=client)


async def test_no_key_stops_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLETAR_JEV_API_KEY", "")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    get_settings.cache_clear()
    seen: list[httpx.Request] = []
    with pytest.raises(JevConfigurationError, match="API key"):
        await evaluate("s", {"q": Noul()}, client=_client(lambda _: _ok({}), seen))
    assert seen == []


# -- stages ------------------------------------------------------------------------


async def test_gate_sends_the_turn_as_state_and_returns_the_probability() -> None:
    seen: list[httpx.Request] = []
    client = _client(lambda _: _ok({GATE_QUESTION_NAME: {"type": "noul", "noul": 0.91}}), seen)

    decision = await gate("I moved to Denver", previous="where do you live now?", client=client)

    body = json.loads(seen[0].content)
    assert body["state"] == {
        "previous_turn": "where do you live now?",
        "latest_turn": "I moved to Denver",
    }
    # The turn is state, never part of the question: text in it cannot re-ask anything.
    assert "Denver" not in json.dumps(body["questions"])
    assert decision.probability == 0.91


async def test_reconcile_offers_exactly_the_four_labels() -> None:
    seen: list[httpx.Request] = []
    answer = {
        "type": "choice",
        "choice": "supersedes",
        "confidence": 0.8,
        "probabilities": {"new": 0.1, "duplicate": 0.05, "supersedes": 0.8, "contradicts": 0.05},
    }
    client = _client(lambda _: _ok({RECONCILE_QUESTION_NAME: answer}), seen)

    decision = await reconcile(
        Statement("Moved to Denver", date(2026, 6, 14)),
        Statement("Lives in Boston", date(2025, 3, 2)),
        client=client,
    )

    body = json.loads(seen[0].content)
    assert tuple(body["questions"][RECONCILE_QUESTION_NAME]["criteria"]) == RECONCILE_LABELS
    assert body["state"]["candidate"] == {"statement": "Moved to Denver", "said_at": "2026-06-14"}
    assert decision.label == "supersedes"
    assert decision.confidence == 0.8


async def test_reconcile_rejects_a_label_it_did_not_offer() -> None:
    answer = {"type": "choice", "choice": "merge", "confidence": 0.9, "probabilities": {}}
    client = _client(lambda _: _ok({RECONCILE_QUESTION_NAME: answer}))
    with pytest.raises(JevConfigurationError):
        await reconcile(Statement("a"), Statement("b"), client=client)


# -- metrics -----------------------------------------------------------------------


def _g(id: str, durable: bool, weight: float = 1.0) -> GateItem:
    return GateItem(id=id, turn=id, durable=durable, weight=weight)


def test_gate_report_finds_the_highest_threshold_meeting_target_recall() -> None:
    scored = [
        (_g("p1", True), 0.9),
        (_g("p2", True), 0.6),
        (_g("p3", True), 0.2),
        (_g("p4", True), 0.8),
        (_g("n1", False), 0.7),
        (_g("n2", False), 0.1),
    ]
    report = gate_report(scored, thresholds=(0.5,), target_recall=0.75)

    row = report.rows[0]
    assert row.recall == 0.75
    assert row.precision == 0.75
    assert row.missed == ("p3",)
    # Three of four positives need a threshold at or below the third-highest (0.6).
    assert report.at_target is not None
    assert report.at_target.threshold == 0.6
    assert report.at_target.recall == 0.75


def test_gate_pass_rate_is_weighted_to_the_population() -> None:
    # One sampled negative standing for nine: passing it costs nine turns' worth.
    scored = [(_g("p", True, weight=1.0), 0.9), (_g("n", False, weight=9.0), 0.9)]
    assert gate_report(scored, thresholds=(0.5,)).rows[0].pass_rate == 1.0
    scored = [(_g("p", True, weight=1.0), 0.9), (_g("n", False, weight=9.0), 0.1)]
    assert gate_report(scored, thresholds=(0.5,)).rows[0].pass_rate == 0.1


def test_unlabelled_items_are_not_scored() -> None:
    scored = [(GateItem(id="x", turn="x"), 0.9), (_g("p", True), 0.9)]
    assert gate_report(scored).n == 1


def _r(id: str, truth: str, predicted: str, confidence: float) -> ReconcileScore:
    item = ReconcileItem(
        id=id,
        candidate=StatementRecord(content="c"),
        existing=StatementRecord(content="e"),
        label=truth,  # type: ignore[arg-type]
    )
    return ReconcileScore(item, predicted, confidence)  # type: ignore[arg-type]


def test_false_supersede_rate_and_the_confidence_floor() -> None:
    scored = [
        _r("a", "supersedes", "supersedes", 0.9),
        _r("b", "new", "supersedes", 0.55),  # wrong, but unsure
        _r("c", "duplicate", "duplicate", 0.9),
        _r("d", "contradicts", "supersedes", 0.95),  # wrong and sure: the worst case
        _r("e", "new", "new", 0.9),
    ]
    report = reconcile_report(scored, floors=(0.0, 0.6))

    assert report.accuracy == 0.6
    assert report.confusion["new"]["supersedes"] == 1
    no_floor, floor = report.rows
    assert no_floor.false_supersede_rate == 2 / 4
    assert no_floor.false_supersedes == ("b", "d")
    # The floor sends "b" to keep-both-and-flag; "d" is confident and still wrong.
    assert floor.false_supersede_rate == 1 / 4
    assert floor.flag_rate == 1 / 5
    assert floor.supersede_recall == 1.0


def test_percentile_is_nearest_rank() -> None:
    assert percentile([10, 20, 30, 40], 50) == 20
    assert percentile([10, 20, 30, 40], 95) == 40
    assert percentile([], 50) == 0.0


# -- sets --------------------------------------------------------------------------


def test_older_extraction_fixtures_load_as_gate_sets() -> None:
    labelled = load_gate_set(FIXTURES / "extraction_set.json")
    assert labelled.items
    assert all(item.durable is not None for item in labelled.items)


def test_reconcile_seed_covers_every_label_with_dated_pairs() -> None:
    seed = load_reconcile_set(FIXTURES / "reconcile_seed.json")
    labels = {item.label for item in seed.items}
    assert labels == set(RECONCILE_LABELS)
    assert len({item.id for item in seed.items}) == len(seed.items)
    for item in seed.items:
        if item.label == "supersedes":
            # A replacement is a later statement; the seed must not teach otherwise.
            assert item.candidate.said_at and item.existing.said_at
            assert item.candidate.said_at > item.existing.said_at
