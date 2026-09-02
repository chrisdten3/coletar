"""M2.2: extraction quality against the labelled turn set.

The build plan asks for one number — a false-positive write rate under 15%. This
measures it, and also measures recall, because an extractor that never fires has a
0% false-positive rate and is worthless. Reporting only the required number would be
technically honest and substantively misleading.

The labelled set and its definition of "durable fact" live in
`fixtures/extraction_set.json`, and were written before either was measured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from coletar.extraction import extract_memories
from coletar.extraction.extractor import extract_with_model
from coletar.schema.objects import ExtractionMethod, MemoryKind, OriginType

FIXTURE = Path(__file__).parent / "fixtures" / "extraction_set.json"

#: Build plan M2.2.
FALSE_POSITIVE_BAR = 0.15

#: The one labelled negative the heuristic still fires on, kept explicit so a
#: regression shows up as a *new* name rather than as a number drifting upward.
#: "I prefer not to say" needs to be told apart from "I prefer not to use Docker",
#: which is a genuine standing preference. That is semantics, and it is the argument
#: for the model-assisted path at M6.2 rather than more regex.
KNOWN_FALSE_POSITIVES = {"n22"}

#: Labelled durable, and deliberately never extracted. Both are identity claims.
#: "My name is X" is either the account holder's own name — which the product
#: already has and gains nothing from storing — or somebody else's, pasted in with
#: the email they were replying to. Measured on a real 17,881-turn export, the
#: pattern fired 17 times and at least seven of those were strangers: recruiters,
#: a founder, students, recorded as first-person facts about the user and then
#: rendered into prompts sent to OpenAI and Anthropic on recall.
#:
#: The labels stay `durable: true` because they *are* true durable facts. Editing
#: ground truth to match the implementation is how an eval stops meaning anything.
#: This is a policy decision costing real recall, so it is named here rather than
#: absorbed into the recall number.
DECLINED_BY_POLICY = {"p03", "p18"}


@dataclass
class Scorecard:
    true_positives: list[str] = field(default_factory=list)
    false_positives: list[str] = field(default_factory=list)
    false_negatives: list[str] = field(default_factory=list)
    true_negatives: list[str] = field(default_factory=list)
    wrong_kind: list[str] = field(default_factory=list)

    @property
    def writes(self) -> int:
        return len(self.true_positives) + len(self.false_positives)

    @property
    def false_positive_rate(self) -> float:
        """Of everything written, the share that should not have been."""
        return len(self.false_positives) / self.writes if self.writes else 0.0

    @property
    def recall(self) -> float:
        durable = len(self.true_positives) + len(self.false_negatives)
        return len(self.true_positives) / durable if durable else 0.0

    def summary(self) -> str:
        return (
            f"writes={self.writes} "
            f"fp_rate={self.false_positive_rate:.1%} "
            f"precision={1 - self.false_positive_rate:.1%} "
            f"recall={self.recall:.1%} "
            f"false_positives={sorted(self.false_positives)} "
            f"missed={sorted(self.false_negatives)} "
            f"wrong_kind={sorted(self.wrong_kind)}"
        )


@pytest.fixture(scope="module")
def labelled_turns() -> list[dict[str, object]]:
    turns: list[dict[str, object]] = json.loads(FIXTURE.read_text())["turns"]
    assert len(turns) >= 50, "the build plan asks for a 50-example set"
    return turns


async def _score(turns: list[dict[str, object]]) -> Scorecard:
    card = Scorecard()
    for turn in turns:
        turn_id = str(turn["id"])
        extracted = await extract_memories(user_text=str(turn["user"]))
        fired = bool(extracted)
        if turn["durable"]:
            if fired:
                card.true_positives.append(turn_id)
                if extracted[0].kind.value != turn["kind"]:
                    card.wrong_kind.append(f"{turn_id}:{extracted[0].kind.value}")
            else:
                card.false_negatives.append(turn_id)
        elif fired:
            card.false_positives.append(turn_id)
        else:
            card.true_negatives.append(turn_id)
    return card


async def test_false_positive_write_rate_is_under_the_bar(labelled_turns):
    card = await _score(labelled_turns)
    assert card.false_positive_rate < FALSE_POSITIVE_BAR, card.summary()


async def test_recall_is_reported_and_not_traded_away_for_precision(labelled_turns):
    """Guards must reject non-assertions, not assertions. If a future guard buys
    precision by suppressing real memories, this is what notices."""
    card = await _score(labelled_turns)
    assert card.recall >= 0.85, card.summary()
    assert set(card.false_negatives) <= DECLINED_BY_POLICY, card.summary()


async def test_every_extracted_memory_is_correctly_typed(labelled_turns):
    card = await _score(labelled_turns)
    assert card.wrong_kind == [], card.summary()


async def test_the_only_false_positives_are_the_known_ones(labelled_turns):
    card = await _score(labelled_turns)
    assert set(card.false_positives) <= KNOWN_FALSE_POSITIVES, card.summary()


# -- the guards, one test each ------------------------------------------------
@pytest.mark.parametrize(
    ("turn", "guard"),
    [
        ("Is it true that I never use semicolons in this file?", "question"),
        ("Do you think I should prefer tabs over spaces?", "question"),
        ("She said 'I always use vim' and I laughed.", "quotation"),
        ("Write a story about a spy who says 'my name is Bond'.", "quotation"),
        ("Someone on the team said from now on we should use tabs.", "attribution"),
        ("The docs say from now on the API requires auth.", "attribution"),
        ("I'm working on it right now.", "anaphora"),
        ("I like this approach a lot.", "anaphora"),
        ("I'm building up my courage to refactor this module.", "particle"),
    ],
)
async def test_each_guard_rejects_its_case(turn: str, guard: str):
    assert await extract_memories(user_text=turn) == [], guard


async def test_a_guard_only_suppresses_its_own_sentence():
    """Guards are sentence-scoped. A question must not silence a real statement
    that happens to share a turn with it."""
    extracted = await extract_memories(
        user_text="Is that right? I prefer fixed-point integers for money."
    )
    assert [m.content for m in extracted] == ["I prefer fixed-point integers for money"]


async def test_an_attribution_after_the_trigger_does_not_suppress_it():
    """'I prefer that you say less' is a preference — the reporting verb has to come
    *before* the claim for the claim to be someone else's."""
    extracted = await extract_memories(user_text="I prefer that you say less.")
    assert len(extracted) == 1


# -- content fidelity ---------------------------------------------------------
@pytest.mark.parametrize(
    ("turn", "expected"),
    [
        ("I never use classes when a function will do.",
         "I never use classes when a function will do"),
        ("I don't use Docker for local development.",
         "I don't use Docker for local development"),
        ("I don't want new dependencies added without asking me first.",
         "I don't want new dependencies added without asking me first"),
    ],
)
async def test_negation_survives_into_the_stored_memory(turn: str, expected: str):
    """Storing "classes when a function will do" from "I never use classes..."
    records the opposite of what the user said. A memory that inverts its source is
    worse than no memory at all."""
    extracted = await extract_memories(user_text=turn)
    assert [m.content for m in extracted] == [expected]


@pytest.mark.parametrize(
    ("turn", "expected"),
    [
        ("Remember that my timezone is US Eastern.", "my timezone is US Eastern"),
        ("From now on, always use uv instead of pip.", "always use uv instead of pip"),
    ],
)
async def test_a_meta_trigger_is_stripped_from_the_memory(turn: str, expected: str):
    """'Remember that' is an instruction to the assistant, not part of the fact."""
    extracted = await extract_memories(user_text=turn)
    assert [m.content for m in extracted] == [expected]


async def test_an_internal_dot_does_not_truncate_the_memory():
    """Sentence splitting keyed on any '.' stored this as 'Ledger deploys to Fly'."""
    extracted = await extract_memories(user_text="Remember that Ledger deploys to Fly.io.")
    assert [m.content for m in extracted] == ["Ledger deploys to Fly.io"]


async def test_extraction_is_tagged_as_an_explicit_user_statement():
    extracted = await extract_memories(user_text="I prefer spaces over tabs.")
    assert extracted[0].extraction_method is ExtractionMethod.EXPLICIT_STATEMENT
    assert extracted[0].provenance.origin_type is OriginType.USER
    assert extracted[0].kind is MemoryKind.PREFERENCE


async def test_the_assistant_reply_is_never_mined():
    """A model's statements about the user are inference, not testimony."""
    assert await extract_memories(
        user_text="Thanks!", assistant_text="I prefer to keep answers short."
    ) == []


async def test_the_model_assisted_path_is_implemented_and_guarded():
    """M6.2 landed. What it must never do is covered in test_extraction_model.py;
    this only pins that the stub is gone and the contract is unchanged."""
    from coletar.extraction.extractor import GROUNDING_FLOOR

    assert 0.0 < GROUNDING_FLOOR <= 1.0
    assert callable(extract_with_model)


# --- pasted text is not testimony -------------------------------------------------
#
# Found by running a real 17,881-turn ChatGPT export through the extractor. The
# sentence-level guards all assume the turn is the user talking; on real usage a
# large share of user turns are pasted email, job descriptions and assignments. The
# names below are invented — the real cases were living people's, which is the whole
# problem.


@pytest.mark.asyncio
async def test_a_pasted_email_does_not_become_a_fact_about_the_reader() -> None:
    """The failure that motivated the guard: a recruiter's introduction, pasted in
    so the assistant could help draft a reply, stored as who the user is."""
    turn = (
        "Hi Sam,\n\n"
        "My name is Dana Whitfield and I manage University Recruiting at Initech. "
        "I work at Initech on the early-careers team and I prefer to set up a call "
        "before sharing the full job description.\n\n"
        "Best regards,\n"
        "Dana"
    )
    assert await extract_memories(user_text=turn) == []


@pytest.mark.asyncio
async def test_an_identity_claim_is_never_extracted() -> None:
    """Either it is the account holder's own name, which the product already has,
    or it is somebody else's. Neither is worth the risk of the second."""
    assert await extract_memories(user_text="My name is Dana Whitfield.") == []


@pytest.mark.asyncio
async def test_a_short_typed_statement_still_extracts() -> None:
    """The guard must cost pasted text, not ordinary use."""
    extracted = await extract_memories(user_text="I prefer fixed-point integers for money.")
    assert [m.content for m in extracted] == ["I prefer fixed-point integers for money"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("turn", "why"),
    [
        ("Hi Sam,\n\nI use Postgres for everything.", "opens like a letter"),
        ("I use Postgres for everything.\n\nBest regards,\n", "closes like a letter"),
        ("I use Postgres for everything. " + "Filler sentence. " * 80, "document length"),
    ],
)
async def test_each_paste_signal_suppresses_the_turn(turn: str, why: str) -> None:
    assert await extract_memories(user_text=turn) == [], why


@pytest.mark.asyncio
async def test_the_guard_reports_which_signal_fired() -> None:
    """A user is entitled to ask why something is not a memory, so the reason is a
    value rather than a bool buried in a branch."""
    from coletar.extraction.extractor import _looks_pasted

    assert _looks_pasted("I prefer tabs.") is None
    assert _looks_pasted("Dear Sam,\nI prefer tabs.") == "opens_like_a_letter"
    assert _looks_pasted("x" * 1001) == "long_enough_to_be_a_document"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "turn",
    [
        "I used to want to be a physical therapist.",
        "I use to want to be a physical therapist.",  # the common misspelling
    ],
)
async def test_a_dropped_habit_is_not_stored_as_a_current_one(turn: str) -> None:
    """`i (?:use|run)` reads "I used to X" as present tense, and the misspelling
    "I use to" matches the trigger exactly. Storing either inverts the meaning:
    the graph asserts a career the user explicitly left."""
    assert await extract_memories(user_text=turn) == []


@pytest.mark.asyncio
async def test_abandoning_one_habit_does_not_suppress_a_live_preference() -> None:
    """Sentence-scoped, like every other guard."""
    extracted = await extract_memories(
        user_text="I used to want to be a physical therapist. I prefer fixed-point integers for money."
    )
    assert [m.content for m in extracted] == ["I prefer fixed-point integers for money"]
