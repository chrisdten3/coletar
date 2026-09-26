"""English in, `HistoryQuery` out (SCOPE §6, §8.2).

This is the only part of the history surface a language model is allowed near,
and what it may produce is one frozen Pydantic object. It never reads the graph,
never sees a memory, and never computes a number. `rollup` does the counting,
deterministically, from the log.

That division is not fussiness. Constraint #7 says stored memory is data and
never instructions; a model given the run of the graph to "answer questions
about it" is a model reading user content and choosing its next action from it.
Compiling to a query instead keeps the model's whole input the user's own
sentence, and makes the interpretation an artifact the user can see and correct
rather than a hidden step they can only re-phrase at.

**The default compiler is rules, not a model.** The grammar is closed -- every
filter is an enum that already exists -- so a vocabulary match covers the
questions people actually ask, runs offline, costs nothing, and is auditable
line by line. `compile_question` returns what it matched *and* what it ignored,
so the UI can say "I read 'last month' and 'by provider', and I did not
understand 'flaky'" instead of quietly answering a different question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from coletar.history.query import (
    Bucket,
    GroupBy,
    HistoryQuery,
    Metric,
    ObjectFilter,
)
from coletar.schema.events import Actor
from coletar.schema.objects import (
    ExtractionMethod,
    LocalityMode,
    MemoryKind,
    ObjectType,
    Provider,
    Sensitivity,
)

#: Phrase -> metric. Ordered longest-first at match time, so "never read" wins
#: over "read".
_METRIC_PHRASES: dict[str, Metric] = {
    "written": Metric.WRITES,
    "wrote": Metric.WRITES,
    "writes": Metric.WRITES,
    "added": Metric.WRITES,
    "created": Metric.WRITES,
    "learned": Metric.WRITES,
    "learn": Metric.WRITES,
    "write": Metric.WRITES,
    "saved": Metric.WRITES,
    "captured": Metric.WRITES,
    "new memories": Metric.WRITES,
    "edited": Metric.REVISIONS,
    "edits": Metric.REVISIONS,
    "revised": Metric.REVISIONS,
    "revisions": Metric.REVISIONS,
    "updated": Metric.REVISIONS,
    "changed": Metric.REVISIONS,
    "retired": Metric.RETIREMENTS,
    "retirements": Metric.RETIREMENTS,
    "deleted": Metric.RETIREMENTS,
    "removed": Metric.RETIREMENTS,
    "superseded": Metric.SUPERSESSIONS,
    "supersessions": Metric.SUPERSESSIONS,
    "corrected": Metric.SUPERSESSIONS,
    "corrections": Metric.SUPERSESSIONS,
    "replaced": Metric.SUPERSESSIONS,
    "contradicted": Metric.SUPERSESSIONS,
    "corroborated": Metric.CORROBORATIONS,
    "confirmed": Metric.CORROBORATIONS,
    "reviewed": Metric.REVIEWS,
    "approved": Metric.REVIEWS,
    "rescoped": Metric.RESCOPES,
    "reach changed": Metric.RESCOPES,
    "restricted": Metric.RESCOPES,
    "retrievals": Metric.RETRIEVALS,
    "retrieved": Metric.RETRIEVALS,
    "searches": Metric.RETRIEVALS,
    "searched": Metric.RETRIEVALS,
    "queries": Metric.RETRIEVALS,
    "asked": Metric.RETRIEVALS,
    "calling": Metric.RETRIEVALS,
    "calls": Metric.RETRIEVALS,
    "reads": Metric.RETRIEVALS,
    "read": Metric.RETRIEVALS,
    "reading": Metric.RETRIEVALS,
    "seen": Metric.RETRIEVALS,
    "using": Metric.RETRIEVALS,
    "served": Metric.OBJECTS_SERVED,
    "handed over": Metric.OBJECTS_SERVED,
    "objects served": Metric.OBJECTS_SERVED,
    "context served": Metric.OBJECTS_SERVED,
    "extraction failures": Metric.EXTRACTION_FAILURES,
    "failed extractions": Metric.EXTRACTION_FAILURES,
    "never read": Metric.NEVER_READ,
    "never been read": Metric.NEVER_READ,
    "unused": Metric.NEVER_READ,
    "unreviewed": Metric.UNREVIEWED,
    "pending review": Metric.UNREVIEWED,
    "needs review": Metric.UNREVIEWED,
    "confidence": Metric.MEAN_CONFIDENCE,
    "how sure": Metric.MEAN_CONFIDENCE,
    "active": Metric.ACTIVE_OBJECTS,
    "total": Metric.ACTIVE_OBJECTS,
    "size": Metric.ACTIVE_OBJECTS,
    "how big": Metric.ACTIVE_OBJECTS,
    "how many facts": Metric.ACTIVE_OBJECTS,
    "how much context": Metric.ACTIVE_OBJECTS,
}

_GROUP_PHRASES: dict[str, GroupBy] = {
    "by actor": GroupBy.ACTOR,
    "by who": GroupBy.ACTOR,
    "per actor": GroupBy.ACTOR,
    "by provider": GroupBy.PROVIDER,
    "by assistant": GroupBy.PROVIDER,
    "by tool": GroupBy.PROVIDER,
    "by model": GroupBy.PROVIDER,
    "per provider": GroupBy.PROVIDER,
    "by surface": GroupBy.SURFACE,
    "by door": GroupBy.SURFACE,
    "by type": GroupBy.OBJECT_TYPE,
    "by object type": GroupBy.OBJECT_TYPE,
    "by kind": GroupBy.MEMORY_KIND,
    "by memory kind": GroupBy.MEMORY_KIND,
    "by extraction method": GroupBy.EXTRACTION_METHOD,
    "by method": GroupBy.EXTRACTION_METHOD,
    "by how": GroupBy.EXTRACTION_METHOD,
    "by origin": GroupBy.EXTRACTION_METHOD,
    "by sensitivity": GroupBy.SENSITIVITY,
    "by reach": GroupBy.LOCALITY_MODE,
    "by locality": GroupBy.LOCALITY_MODE,
    "by scope": GroupBy.SCOPE,
    "by project": GroupBy.SCOPE,
}

_BUCKET_PHRASES: dict[str, Bucket] = {
    "per day": Bucket.DAY,
    "a day": Bucket.DAY,
    "daily": Bucket.DAY,
    "each day": Bucket.DAY,
    "by day": Bucket.DAY,
    "per week": Bucket.WEEK,
    "a week": Bucket.WEEK,
    "weekly": Bucket.WEEK,
    "each week": Bucket.WEEK,
    "by week": Bucket.WEEK,
    "per month": Bucket.MONTH,
    "a month": Bucket.MONTH,
    "monthly": Bucket.MONTH,
    "each month": Bucket.MONTH,
    "by month": Bucket.MONTH,
}

_PROVIDER_WORDS: dict[str, Provider] = {
    "claude": Provider.CLAUDE,
    "chatgpt": Provider.CHATGPT,
    "openai": Provider.CHATGPT,
    "gpt": Provider.CHATGPT,
    "gemini": Provider.GEMINI,
    "local": Provider.LOCAL,
    "coleta": Provider.COLETAR,
    "coletar": Provider.COLETAR,
}

_ACTOR_WORDS: dict[str, Actor] = {
    "the model": Actor.MODEL,
    "a model": Actor.MODEL,
    "model-written": Actor.MODEL,
    "the assistant": Actor.MODEL,
    "i": Actor.USER,
    "me": Actor.USER,
    "myself": Actor.USER,
    "by hand": Actor.USER,
    "the job": Actor.JOB,
    "a job": Actor.JOB,
    "the batch": Actor.JOB,
    "the importer": Actor.MIGRATION,
    "the migration": Actor.MIGRATION,
    "a connector": Actor.CONNECTOR,
    "the compiler": Actor.COMPILER,
}

_TYPE_WORDS: dict[str, ObjectType] = {
    "memories": ObjectType.MEMORY,
    "memory": ObjectType.MEMORY,
    "decisions": ObjectType.DECISION,
    "decision": ObjectType.DECISION,
    "projects": ObjectType.PROJECT,
    "conversations": ObjectType.CONVERSATION,
    "artifacts": ObjectType.ARTIFACT,
    "entities": ObjectType.ENTITY,
    "episodes": ObjectType.EPISODE,
    "captured turns": ObjectType.EPISODE,
}

_KIND_WORDS: dict[str, MemoryKind] = {
    "preferences": MemoryKind.PREFERENCE,
    "preference": MemoryKind.PREFERENCE,
    "instructions": MemoryKind.INSTRUCTION,
    "instruction": MemoryKind.INSTRUCTION,
    "goals": MemoryKind.GOAL,
    "goal": MemoryKind.GOAL,
    "inferences": MemoryKind.INFERENCE,
    "inferred": MemoryKind.INFERENCE,
    "guesses": MemoryKind.INFERENCE,
}

_METHOD_WORDS: dict[str, ExtractionMethod] = {
    "imported": ExtractionMethod.ACCOUNT_EXPORT_PARSE,
    "from an export": ExtractionMethod.ACCOUNT_EXPORT_PARSE,
    "from my export": ExtractionMethod.ACCOUNT_EXPORT_PARSE,
    "mined": ExtractionMethod.ACCOUNT_EXPORT_PARSE,
    "curated": ExtractionMethod.PROVIDER_CURATED,
    "i said": ExtractionMethod.EXPLICIT_STATEMENT,
    "explicitly": ExtractionMethod.EXPLICIT_STATEMENT,
}

_SENSITIVITY_WORDS: dict[str, Sensitivity] = {
    "sensitive": Sensitivity.SENSITIVE,
    "private": Sensitivity.SENSITIVE,
}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

#: Words that carry no query signal. Anything left over after matching that is
#: not one of these gets reported back as "not understood", which is the whole
#: honesty mechanism -- see `CompiledQuestion.unmatched`.
_NOISE_WORDS = """
    a about all an and any are as at average avg be been being by can chart coleta coletar
    context count did display do does each fact facts find for from get give go graph
    graphs had has have history how i in into is it its let list look many me mean much my
    number of on or our over per please plot see series should show shows so stuff sum tell
    that the their them then there these thing things this time to total trend trends us
    view was we were what when where which who why will with workspace you your
    """

_NOISE = frozenset(_NOISE_WORDS.split())


@dataclass
class CompiledQuestion:
    """A question, the query it became, and an account of the translation."""

    question: str
    query: HistoryQuery
    #: (phrase found, what it set) -- rendered in the UI under the controls.
    matched: list[tuple[str, str]] = field(default_factory=list)
    #: Content words the compiler did not use for anything.
    unmatched: list[str] = field(default_factory=list)
    #: False when nothing but defaults matched, so the UI can offer the question
    #: back rather than charting an answer to a question nobody asked.
    understood: bool = True
    compiler: str = "rules"

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "query": self.query.model_dump(mode="json"),
            "explanation": self.query.describe(),
            "matched": [{"phrase": p, "meaning": m} for p, m in self.matched],
            "unmatched": self.unmatched,
            "understood": self.understood,
            "compiler": self.compiler,
        }


class QuestionCompiler(Protocol):
    """So a model-backed compiler can replace the rules without anything above
    this module noticing. Both must return a `CompiledQuestion`: whatever
    produced the query, the user is shown the query."""

    def compile(self, question: str, *, now: datetime | None = None) -> CompiledQuestion: ...


def _boundary(phrase: str) -> str:
    """A phrase match that cannot land inside a longer word.

    Both edges, not just the left one. With a left-only lookbehind, "me" matched
    the middle of "mean confidence" and quietly added an actor filter to a query
    about averages -- exactly the class of silent misreading this compiler exists
    to make visible.
    """
    return rf"(?<![a-z0-9]){re.escape(phrase.strip())}(?![a-z0-9])"


def _find(text: str, table: dict[str, Any]) -> list[tuple[str, Any]]:
    """Longest phrase first, so "never read" is not consumed by "read"."""
    hits: list[tuple[str, Any]] = []
    for phrase in sorted(table, key=len, reverse=True):
        if re.search(_boundary(phrase), text):
            hits.append((phrase, table[phrase]))
    return hits


def _unique(hits: list[tuple[str, Any]]) -> list[Any]:
    """Values in match order, deduplicated -- two phrases for one enum member
    ("the model", "model-written") must not read "actor is model or model"."""
    seen: list[Any] = []
    for _, value in hits:
        if value not in seen:
            seen.append(value)
    return seen


def _parse_window(text: str, now: datetime) -> tuple[int | None, datetime | None, str | None]:
    """Returns (window_days, explicit since, phrase matched)."""
    match = re.search(r"(?:last|past|previous)\s+(\d+)\s*(day|week|month|year)s?", text)
    if match:
        count, unit = int(match.group(1)), match.group(2)
        days = count * {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
        return days, None, match.group(0)

    for phrase, days in (
        ("last week", 7),
        ("this week", 7),
        ("last month", 30),
        ("this month", 30),
        ("last quarter", 90),
        ("this quarter", 90),
        ("last year", 365),
        ("this year", 365),
        ("yesterday", 2),
        ("today", 1),
        ("all time", 730),
        ("ever", 730),
    ):
        if re.search(_boundary(phrase), text):
            return days, None, phrase

    for name, number in _MONTHS.items():
        if f"since {name}" in text:
            year = now.year if number <= now.month else now.year - 1
            return None, datetime(year, number, 1, tzinfo=UTC), f"since {name}"

    match = re.search(r"since\s+(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        y, m, d = (int(g) for g in match.groups())
        return None, datetime(y, m, d, tzinfo=UTC), match.group(0)

    return None, None, None


def _parse_subject(text: str) -> tuple[str | None, str | None]:
    """The `contains` filter: a quoted string, or the tail of "about ...".

    Quotes win, because a user who quoted something was being explicit about
    where the phrase ends and the compiler should not second-guess it.
    """
    quoted = re.search(r"[\"“']([^\"”']{2,60})[\"”']", text)
    if quoted:
        return quoted.group(1).strip(), quoted.group(0)

    match = re.search(
        r"\b(?:about|mentioning|regarding|concerning|related to|on the topic of)\s+"
        r"([a-z0-9][a-z0-9 _-]{1,40}?)"
        r"(?=\s+(?:per|by|last|this|since|over|in|from|grouped|and|$)|[,.?]|$)",
        text,
    )
    if match:
        return match.group(1).strip(), match.group(0)
    return None, None


def compile_question(question: str, *, now: datetime | None = None) -> CompiledQuestion:
    """Turn one English question into a typed query, deterministically."""
    moment = now or datetime.now(UTC)
    text = " " + re.sub(r"\s+", " ", question.lower().strip()) + " "
    # "show me" / "tell me" are how people address the tool, not a statement
    # about who authored anything. Left in, every such question silently
    # acquired an actor filter.
    text = re.sub(r"\b(?:show|tell|give|get)\s+me\b", " ", text)
    matched: list[tuple[str, str]] = []
    consumed: list[str] = []

    def take(phrase: str, meaning: str) -> None:
        matched.append((phrase, meaning))
        consumed.append(phrase)

    # Subject first: it eats a span that would otherwise be mined for keywords,
    # and "corrections about the launch date" must not read "date" as a filter.
    subject, subject_phrase = _parse_subject(text)
    if subject_phrase:
        take(subject_phrase.strip(), f"text contains “{subject}”")
        text = text.replace(subject_phrase, " ")

    # Confidence is resolved before the metric and removed from the text.
    # "low confidence facts written by the model" is a question about writes
    # with a confidence filter, but "confidence" is also the name of a metric,
    # and left in place it won the match and charted an average instead.
    confidence_max: float | None = None
    confidence_min: float | None = None
    low = re.search(r"\b(?:low[- ]confidence|unsure|shaky|uncertain|doubtful)\b", text)
    if low:
        confidence_max = 0.6
        take(low.group(0), "confidence ≤ 0.60")
        text = text.replace(low.group(0), " ")
    high = re.search(r"\b(?:high[- ]confidence|certain|solid|confident)\b", text)
    if high:
        confidence_min = 0.85
        take(high.group(0), "confidence ≥ 0.85")
        text = text.replace(high.group(0), " ")

    metric_hits = _find(text, _METRIC_PHRASES)
    metric = metric_hits[0][1] if metric_hits else Metric.WRITES
    if metric_hits:
        take(metric_hits[0][0], f"metric = {metric.value}")

    group_hits = _find(text, _GROUP_PHRASES)
    group_by = group_hits[0][1] if group_hits else GroupBy.NONE
    if group_hits:
        take(group_hits[0][0], f"grouped by {group_by.value}")

    bucket_hits = _find(text, _BUCKET_PHRASES)
    bucket = bucket_hits[0][1] if bucket_hits else Bucket.WEEK
    if bucket_hits:
        take(bucket_hits[0][0], f"bucketed by {bucket.value}")

    window_days, since, window_phrase = _parse_window(text, moment)
    if window_phrase:
        take(window_phrase, f"window = {window_phrase}")

    def collect(table: dict[str, Any], label: str) -> list[Any]:
        """Match one vocabulary once, record every phrase, return unique values."""
        hits = _find(text, table)
        for phrase, value in hits:
            take(phrase, f"{label} = {value}")
        return _unique(hits)

    providers = collect(_PROVIDER_WORDS, "provider")
    actors = collect(_ACTOR_WORDS, "actor")
    types = collect(_TYPE_WORDS, "type")
    kinds = collect(_KIND_WORDS, "kind")
    methods = collect(_METHOD_WORDS, "extracted by")
    sensitivities = collect(_SENSITIVITY_WORDS, "sensitivity")

    locality_modes: list[LocalityMode] = []
    reach = re.search(r"\b(?:local[- ]only|restricted|held back|withheld|kept local)\b", text)
    if reach:
        locality_modes = [LocalityMode.LOCAL_ONLY]
        take(reach.group(0), "reach = local_only")

    # A provider word next to a retrieval metric is "who asked", not "who wrote".
    surfaces: list[str] = []
    if metric in (Metric.RETRIEVALS, Metric.OBJECTS_SERVED) and providers:
        surfaces = [str(p) for p in providers]
        providers = []

    query = HistoryQuery(
        metric=metric,
        group_by=group_by,
        bucket=bucket,
        since=since,
        window_days=window_days or (90 if since is None else 730),
        filter=ObjectFilter(
            types=types,
            kinds=kinds,
            methods=methods,
            actors=actors,
            providers=providers,
            sensitivities=sensitivities,
            locality_modes=locality_modes,
            surfaces=surfaces,
            confidence_min=confidence_min,
            confidence_max=confidence_max,
            contains=subject,
        ),
    )

    leftover = text
    for phrase in consumed:
        # Boundary-aware, for the same reason matching is: a plain replace of
        # "me" turned the unconsumed remainder of "memories" into "mories" and
        # reported it as a word the compiler did not understand.
        leftover = re.sub(_boundary(phrase), " ", leftover)
    unmatched = [
        word
        for word in re.findall(r"[a-z][a-z0-9_-]{2,}", leftover)
        if word not in _NOISE
    ]

    return CompiledQuestion(
        question=question.strip(),
        query=query,
        matched=matched,
        unmatched=sorted(set(unmatched)),
        understood=bool(metric_hits or group_hits or subject or window_phrase),
    )


#: Offered in the UI when a workspace is new, and used by the tests as the
#: worked examples the grammar is required to keep handling.
EXAMPLE_QUESTIONS: list[str] = [
    "how many memories did I write per week last quarter",
    "retrievals by provider over the last 30 days",
    "corrections about the ledger rewrite per month",
    "what has Claude been reading this month",
    "low confidence facts written by the model",
    "show me writes by extraction method since March",
    "which facts have never been read",
    "mean confidence per week by type",
]


def model_compiler_unavailable() -> None:
    """Model-assisted compilation is M9 (see docs/ROADMAP.md).

    Deliberately not a silent fallback to the rules compiler: a user who turned
    on a model backend and got keyword matching would have no way to tell, and
    "it quietly did something else" is the failure this whole surface is built
    to avoid.
    """
    raise NotImplementedError(
        "Model-assisted question compilation is not implemented; see docs/ROADMAP.md "
        "M9. The rules compiler in coletar.history.nl.compile_question is the "
        "supported path and requires no provider."
    )
