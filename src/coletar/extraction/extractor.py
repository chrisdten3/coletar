"""Normalization / Extraction Layer (SCOPE §5).

Per §5 this is where most of the real engineering risk lives: turning a raw export
or a live turn into correctly-typed, correctly-scoped, correctly-confidence-scored
objects is a much harder problem than "chunk and embed."

What runs on the live-turn path is a deliberately conservative heuristic. It fires
only on unambiguous first-person declarations, and it is guarded against the seven
ways a keyword match is *not* an assertion by the user -- which is what the M2.2
labelled set measures.

Two of those guards exist because of M3.4. The M2.2 set was clean conversational
turns, and against a real Claude Code transcript the extractor scored **0%**: every
match was a first-person sentence quoted inside pasted JSON, or a prompt template
full of `[placeholders]`. A developer's transcript is a different domain from a chat,
and precision measured on one does not transfer to the other.

Precision over recall is not a slogan here: a wrong memory costs the user a deletion
and some trust, a missing one costs almost nothing.

Measured against `tests/fixtures/extraction_set.json`; the harness and the current
numbers are in `tests/test_extraction_quality.py` and docs/EXTRACTION.md.
"""

from __future__ import annotations

import re
from enum import StrEnum

from coletar.schema.objects import (
    GLOBAL_SCOPE,
    ExtractionMethod,
    Memory,
    MemoryKind,
    OriginType,
    Provider,
    Scope,
)


class Trigger(StrEnum):
    """What the matched phrase *is*, which decides what gets stored.

    A meta-trigger is an instruction aimed at the assistant -- "remember that",
    "from now on" -- and is no part of the fact, so only the body is kept. An
    assertion trigger is part of the statement itself, and dropping it changes the
    meaning: "I never use classes when a function will do" stored as its body alone
    reads as a preference *for* classes. Storing the inverse of what someone said is
    worse than storing nothing.
    """

    META = "meta"
    ASSERTION = "assertion"


# Only unambiguous first-person declarations. Precision over recall: a wrong memory
# is worse than a missing one, because the user has to find and delete it.
_PATTERNS: list[tuple[re.Pattern[str], MemoryKind, Trigger]] = [
    (re.compile(r"\bremember that\s+(?P<body>.+)", re.I), MemoryKind.FACT, Trigger.META),
    (re.compile(r"\b(?:from now on|going forward),?\s+(?P<body>.+)", re.I),
     MemoryKind.INSTRUCTION, Trigger.META),
    (re.compile(r"\bmy name is\s+(?P<body>.+)", re.I), MemoryKind.FACT, Trigger.ASSERTION),
    (re.compile(r"\bi work (?:at|for)\s+(?P<body>.+)", re.I),
     MemoryKind.FACT, Trigger.ASSERTION),
    (re.compile(r"\bi (?:prefer|like|always use)\s+(?P<body>.+)", re.I),
     MemoryKind.PREFERENCE, Trigger.ASSERTION),
    # Negated: the trigger carries the negation, so it must survive into the content.
    (re.compile(r"\bi (?:never|don't|do not) (?:use|want)\s+(?P<body>.+)", re.I),
     MemoryKind.PREFERENCE, Trigger.ASSERTION),
    (re.compile(r"\bi'?m (?:working on|building)\s+(?P<body>.+)", re.I),
     MemoryKind.GOAL, Trigger.ASSERTION),
    # `no,?` and the optional comma after `actually` both matter: "Actually, it's
    # Globex" is the ordinary way people write a correction.
    (re.compile(r"\b(?:actually|no),?\s*(?:it'?s|i meant)\s+(?P<body>.+)", re.I),
     MemoryKind.CORRECTION, Trigger.ASSERTION),
    # M6.1. The eight patterns above were tuned on live proxy turns, where people
    # write "I prefer X". An account export is years of a different register — a
    # standing instruction to the assistant, a decision the team took, a tool the
    # user simply uses — and against a 100-turn export set the extractor fired on 4
    # of 35 durable statements. The additions below are the forms that recur across
    # *all* surfaces, not shapes reverse-engineered from one fixture.
    #
    # An imperative addressed to the assistant. Anchored to a sentence start so
    # "I would always use X" and "never mind" cannot reach it.
    (re.compile(r"^(?:please\s+)?(?P<body>(?:always|never)\s+\S+.*)", re.I),
     MemoryKind.INSTRUCTION, Trigger.META),
    # A decision already taken, stated in the first person plural. `Decision` is an
    # ObjectType, not a MemoryKind, so this lands as a FACT about the project —
    # which is what it is. "We should" and "we could" deliberately do not match.
    (re.compile(r"\bwe (?:decided|settled|standardi[sz]ed|agreed)\b\s*(?P<body>.+)", re.I),
     MemoryKind.FACT, Trigger.ASSERTION),
    # Present-tense habitual use of a named thing. Weaker than "I prefer", so the
    # guards below carry more of the work here.
    (re.compile(r"\bi (?:use|run)\s+(?P<body>.+)", re.I),
     MemoryKind.PREFERENCE, Trigger.ASSERTION),
]

_MAX_LEN = 400

# A period only ends a sentence when whitespace or the end of the text follows it.
# Without the lookahead, "Ledger deploys to Fly.io" is two sentences and the memory
# gets stored truncated at "Fly".
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Quoted spans. A bare apostrophe is not a quote -- "I'm", "don't" and "it's" all
# contain one -- so a single quote only opens a span when no letter precedes it.
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”|(?<![A-Za-z])'[^']*'(?![A-Za-z])")

#: Someone else asserting something is not the user asserting it.
_ATTRIBUTION = re.compile(
    r"\b(?:said|says|say|saying|told|tells|telling|asked|asks|wrote|writes|"
    r"claims|claimed|argues|argued|mentioned|mentions|suggests|suggested|"
    r"according to)\b",
    re.I,
)

#: A memory whose subject only exists in the surrounding conversation is
#: meaningless once stored. "I'm working on it" is not a goal anyone can act on
#: later; neither is "I like this approach".
#:
#: "that" is deliberately absent. It is ambiguous between a demonstrative ("I like
#: that approach" -- anaphoric) and a complementizer ("I prefer that you say less"
#: -- a real standing preference), and suppressing the second to catch the first
#: trades a genuine memory for a duplicate of what the question guard already
#: rejects.
_ANAPHORIC_HEADS = frozenset({"it", "this", "these", "those", "them", "they", "there", "here"})

#: A particle changes the verb. "building up my courage" is not "building" a thing,
#: and "working out" is not "working on" a project.
_PARTICLE_HEADS = frozenset({"up", "out", "off", "over", "down", "away", "back"})

_WORD = re.compile(r"[A-Za-z0-9']+")

#: Residue of JSON, code or markup around a match. A first-person sentence that ends
#: in `"}]` was never a sentence — it was a string literal inside a structure the
#: user pasted. Measured against a real Claude Code transcript, where every single
#: extraction was of this kind (see docs/EXTRACTION.md).
_STRUCTURAL = re.compile(r'("\s*[}\])]|[{\[]\s*"|"\s*:|\\n|</?\w+>)')

#: A bracketed placeholder is a template someone pasted, not a statement they made:
#: "I'm working on [the larger task] for [who it's for]".
_PLACEHOLDER = re.compile(r"[\[<]\s*[a-z][^\]>]{2,40}\s*[\]>]")


def _sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_SPLIT.split(text.strip()) if part.strip()]


def _quoted_spans(sentence: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _QUOTED.finditer(sentence)]


def _clean(body: str) -> str:
    """Trim the sentence terminator and trailing punctuation.

    Sentences are split before matching now, so the body arrives carrying its own
    full stop -- and a memory reading "Chris." rather than "Chris" is the kind of
    small ugliness that shows up in every compiled artifact downstream.
    """
    return body.strip().strip(" ,;:—-").rstrip(".!?").strip()[:_MAX_LEN]


def _rejected(sentence: str, match: re.Match[str]) -> bool:
    """The five ways a keyword match is not a durable assertion by the user.

    Each of these was a measured false positive on the labelled set, and each is a
    property of the sentence rather than of a particular phrase, so the guard
    generalises past the example that motivated it.
    """
    # 1. A question asks; it does not assert. "Is it true that I never use
    #    semicolons?" is not a preference.
    if sentence.rstrip().endswith("?"):
        return True

    # 2. The first person inside a quotation is not the user. "She said 'I always
    #    use vim'" is a fact about her, if anyone.
    start = match.start()
    if any(open_ <= start < close for open_, close in _quoted_spans(sentence)):
        return True

    # 3. An assertion attributed to someone else is theirs, not the user's. The verb
    #    has to precede the trigger -- "I prefer that you say less" is still a
    #    preference.
    attribution = _ATTRIBUTION.search(sentence[:start])
    if attribution is not None:
        return True

    body = match.group("body")
    # 4. Structure around the match means this is quoted or pasted material, not
    #    something the user wrote as a sentence. Checked on the whole sentence,
    #    because the residue often sits either side of the phrase that matched.
    if _STRUCTURAL.search(sentence):
        return True
    # 5. A bracketed placeholder is a template, not a statement.
    if _PLACEHOLDER.search(sentence):
        return True

    head = next(iter(_WORD.findall(body.lower())), "")
    # 6. Anaphora: the memory would not survive leaving this conversation.
    # 7. A particle makes it a different verb.
    return head in _ANAPHORIC_HEADS or head in _PARTICLE_HEADS


async def extract_memories(
    *,
    user_text: str,
    assistant_text: str = "",
    scope: Scope = GLOBAL_SCOPE,
    provider: Provider = Provider.LOCAL,
) -> list[Memory]:
    """Extract durable memories from one conversational turn.

    Only the user's own words are mined. The assistant's reply is passed in for
    future use by a model-assisted path (which needs the exchange to resolve
    referents) but is never itself treated as a source of fact -- a model's
    statements about the user are inference, not testimony.
    """
    del assistant_text  # reserved for the model-assisted path; see docs/EXTRACTION.md

    found: list[Memory] = []
    seen: set[str] = set()
    # Guards are sentence-scoped, so a question in one sentence cannot suppress an
    # assertion in the next, and vice versa.
    for sentence in _sentences(user_text):
        for pattern, kind, trigger in _PATTERNS:
            for match in pattern.finditer(sentence):
                if _rejected(sentence, match):
                    continue
                # An assertion trigger is part of the claim, so the stored memory
                # starts where the match starts, not where its body does.
                raw = match.group(0) if trigger is Trigger.ASSERTION else match.group("body")
                body = _clean(raw)
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
    """Model-assisted typed extraction with confidence scoring and dedup/merge.

    **M6.2**, not M2.2. The heuristic above clears M2.2's false-positive bar on the
    labelled live-turn set, so a model on the live path would be speculative work --
    it is where an export gets parsed that a model becomes necessary, because a raw
    ChatGPT archive is prose with no reliable first-person surface forms to key on
    and the bar there is 85% precision over messy text.

    Note the cost risk named in §11: this runs on every export at consumer scale, so
    it must be modelled before the consumer tier is priced. The local wedge should
    run it against the user's own local model, where inference is free.
    """
    raise NotImplementedError("Model-assisted extraction is M6.2; see docs/ROADMAP.md")
