"""Following one subject through the graph over time (SCOPE §6, §8.2).

`rollup` answers questions *about* the graph -- how much was written, who read
it, how confident it is. This answers questions about what the graph **says**:
"how has my choice of database changed", "what did I decide about deployment and
when". Different question, different evidence, and worth keeping apart, because
one is measured and the other is read.

Everything here is deterministic. A model may later narrate a thread (AGENTS.md
§1, amended 2026-09-26), but it narrates *this* -- the switches, the dates and
the object ids are computed from the log first, handed to the model as
established fact, and re-checked before anything renders. That ordering is the
whole design: the expensive, fallible step gets the smallest job, and the claim
a user is most likely to act on ("you moved off Postgres 15 in June") is the one
nothing was asked to guess.

Three things are extracted, in descending order of how much they can be trusted:

  * **Switches.** A supersession chain where the named alternatives differ
    between one statement and the next. This is a change the *user recorded as a
    correction*, so it is evidence rather than inference, and it carries the
    event id that proves it.
  * **Alternative mass.** How many active objects mention each competing name,
    per bucket. Weaker -- a mention is not an endorsement -- but it shows a
    position fading before it was ever formally replaced, which switches cannot.
  * **Nothing else.** In particular no sentiment, no inferred "preference
    strength", and no model-free narrative. A thread that found one switch says
    it found one switch.

**The honest limit**, because it will show up the first time this runs against a
real corpus rather than a seeded one: a switch is only visible if somebody wrote
the new position *as replacing* the old one. A graph full of unrelated facts
that happen to disagree has no chain to walk, and this will find very little in
it. That gap is upstream in extraction, not here.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from coletar.history.query import Bucket
from coletar.retrieval.embedding import build_embedder, cosine, l2_normalize, stem, tokenize
from coletar.schema.objects import ContextObject, ObjectType
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: Objects one thread may gather. Also the number that makes AGENTS.md §1's
#: "one subject's objects, never the whole graph" true rather than aspirational
#: -- a model backend is handed this set and nothing else.
DEFAULT_MAX_OBJECTS = 120

_EXCLUDED = frozenset({ObjectType.EPISODE})

#: "Postgres 15", "Claude 3" -- a capitalised name followed by a version. Caught
#: as a unit because the version *is* the choice: an alternative series that
#: collapsed "Postgres 15" and "Postgres 16" into "postgres" would chart a
#: migration as a flat line.
_VERSIONED = re.compile(r"\b([A-Z][A-Za-z.]{1,20})\s+(\d+(?:\.\d+)?)\b")

#: A single token that looks like a product rather than a word: an internal
#: capital (CapIQ, FactSet), a digit (qwen2.5, GPT-4), or a dot or hyphen inside
#: it (Fly.io). The uppercase-or-digit requirement on the separator forms is
#: load-bearing: without it, "per-service" and "two-week" were being offered as
#: competing products a user had switched between.
_NAMEY = re.compile(
    r"\b("
    r"[A-Z][A-Za-z0-9]*(?:[.\-][A-Za-z0-9]+)+"   # Fly.io, GPT-4, S3-backed
    r"|[A-Za-z]+\d[\w.]*"                        # qwen2.5, llama3
    r"|[A-Z][a-z]+[A-Z]\w*"                      # CapIQ, FactSet, PowerPoint
    r")\b"
)

#: Two capitalised words in a row, so "Claude Sonnet" is one alternative rather
#: than two. The components are then subsumed, for the same reason "Postgres"
#: is subsumed by "Postgres 16": the pair is the choice, the parts are the
#: category, and charting both counts one migration twice.
_PROPER_BIGRAM = re.compile(r"\b([A-Z][a-zA-Z]{2,})\s+([A-Z][a-zA-Z]{2,})\b")

#: Capitalised mid-sentence, which is how most product names and acronyms read.
#: Acronyms stay in deliberately -- RDS, ECS and DMS are real choices here.
_PROPER = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z]{2,})\b")

#: Words that pass the shape tests and mean nothing as alternatives.
_NOT_A_CHOICE_WORDS = """The This That These Those We Our It Its They Their There Here When Where
    What Which Who Why How And But For Nor Yet So Because After Before Until
    Every Each Both All Any Some None One Two Three Anything Nothing Something
    Also Only Just Still Now Then Once Never Always Everything
    January February March April May June July August September October November
    December Monday Tuesday Wednesday Thursday Friday Saturday Sunday"""

_NOT_A_CHOICE = frozenset(_NOT_A_CHOICE_WORDS.split())


def _roots(name: str) -> set[str]:
    """The shorter forms a compound name makes redundant."""
    parts: set[str] = set()
    if " " in name:
        parts.update(name.split())
    for separator in (".", "-"):
        if separator in name:
            parts.add(name.split(separator)[0])
    return {p for p in parts if p and not p.isdigit()}


def dedupe_names(names: set[str]) -> set[str]:
    """Drop every name that a longer one already covers.

    Applied to a whole thread and not just to one sentence: one statement says
    "Fly.io" and the next says "Fly", and keeping both draws two series for one
    decision.
    """
    covered: set[str] = set()
    for name in names:
        covered |= _roots(name)
    return {n for n in names if n not in covered}


def _utc(when: datetime) -> datetime:
    return when.astimezone(UTC) if when.tzinfo else when.replace(tzinfo=UTC)


def candidate_names(text: str) -> set[str]:
    """Named alternatives mentioned in one statement.

    Shape-based rather than dictionary-based on purpose: a fixed list of
    products is wrong the week someone adopts one that is not on it, and this
    has to work for a user whose choices are in a domain nobody anticipated --
    a legal research provider as readily as a database.
    """
    found: set[str] = set()
    for name, version in _VERSIONED.findall(text):
        if name not in _NOT_A_CHOICE:
            found.add(f"{name} {version}")
    for first, second in _PROPER_BIGRAM.findall(text):
        if first not in _NOT_A_CHOICE and second not in _NOT_A_CHOICE:
            found.add(f"{first} {second}")
    for pattern in (_NAMEY, _PROPER):
        for match in pattern.findall(text):
            token = match if isinstance(match, str) else match[0]
            if token not in _NOT_A_CHOICE and len(token) > 2:
                found.add(token)
    return dedupe_names(found)


@dataclass(frozen=True)
class Switch:
    """One recorded change of position, with the event that proves it."""

    at: datetime
    from_object_id: str
    to_object_id: str
    event_id: str
    before: str
    after: str
    dropped: list[str]
    adopted: list[str]
    actor: str
    provider: str

    @property
    def is_substantive(self) -> bool:
        """Did a named alternative actually change?

        A correction that only rewords ("...as of the most recent review") is a
        revision, not a switch, and listing it as one would bury the four that
        matter under forty that do not.
        """
        return bool(self.dropped or self.adopted)

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "from_object_id": self.from_object_id,
            "to_object_id": self.to_object_id,
            "event_id": self.event_id,
            "before": self.before,
            "after": self.after,
            "dropped": self.dropped,
            "adopted": self.adopted,
            "actor": self.actor,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class AlternativeSeries:
    """One competing name, and how many active objects mentioned it per bucket."""

    name: str
    first_seen: datetime
    last_seen: datetime
    total: int
    points: list[tuple[datetime, int, list[str]]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "total": self.total,
            "points": [
                {"at": at.isoformat(), "value": value, "object_ids": ids}
                for at, value, ids in self.points
            ],
        }


@dataclass(frozen=True)
class ThreadReport:
    subject: str
    object_ids: list[str]
    switches: list[Switch]
    alternatives: list[AlternativeSeries]
    timeline: list[dict[str, Any]]
    bucket: str
    start: datetime
    end: datetime
    #: True when the gather hit `max_objects`, so the UI can say the thread is
    #: a sample rather than letting it read as the whole picture.
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "object_ids": self.object_ids,
            "switches": [s.as_dict() for s in self.switches],
            "alternatives": [a.as_dict() for a in self.alternatives],
            "timeline": self.timeline,
            "bucket": self.bucket,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "truncated": self.truncated,
            "matched": len(self.object_ids),
        }


def _relevance(subject_terms: set[str], obj: ContextObject) -> float:
    """Lexical overlap, stemmed. Deliberately simple and predictable: this is
    one half of the gather and the embedding pass is the half that generalises."""
    if not subject_terms:
        return 0.0
    terms = {stem(t) for t in tokenize(obj.content)}
    if not terms:
        return 0.0
    return len(subject_terms & terms) / len(subject_terms)


async def gather(
    store: Store,
    tenant_id: TenantId,
    *,
    subject: str = "",
    anchor_ids: list[str] | None = None,
    max_objects: int = DEFAULT_MAX_OBJECTS,
) -> tuple[list[ContextObject], bool]:
    """The objects a thread is about.

    Retired and superseded objects are included, which is the opposite of what
    every other read path does and the entire point here: a thread whose gather
    dropped the position that was replaced would be a history of the present.

    Two entry points. `anchor_ids` starts from known objects and expands, which
    is what a suggestion uses -- it cannot miss the chain it was derived from.
    `subject` is free text, matched lexically and by embedding, which is what a
    typed question uses and what a model backend would drive.
    """
    everything = [
        obj
        for obj in await store.list_objects(
            tenant_id, limit=4000, include_retired=True, include_superseded=True
        )
        if obj.type not in _EXCLUDED and obj.content.strip()
    ]
    by_id = {obj.id: obj for obj in everything}
    anchors = [by_id[i] for i in (anchor_ids or []) if i in by_id]

    scored: dict[str, float] = {a.id: 10.0 for a in anchors}

    # The whole supersession chain around every anchor, at full weight. A thread
    # that included the newest statement of a position but not the one it
    # replaced could not detect a single switch.
    forward = {obj.supersedes: obj.id for obj in everything if obj.supersedes}
    backward = {obj.id: obj.supersedes for obj in everything if obj.supersedes}
    for anchor in list(anchors):
        cursor: str | None = anchor.id
        while (cursor := backward.get(cursor or "")) and cursor in by_id:
            scored[cursor] = 10.0
        cursor = anchor.id
        while (cursor := forward.get(cursor or "")) and cursor in by_id:
            scored[cursor] = 10.0

    subject_terms = {stem(t) for t in tokenize(subject)} if subject else set()
    if subject_terms:
        for obj in everything:
            if (score := _relevance(subject_terms, obj)) > 0:
                scored[obj.id] = max(scored.get(obj.id, 0.0), score)

    # Semantic expansion, from the subject text and from the anchors alike, so a
    # thread about "backend infrastructure" reaches "the ledger runs on RDS"
    # -- which shares no word with the question.
    seeds = [subject] if subject else []
    seeds += [a.content for a in anchors]
    if seeds:
        embedder = build_embedder()
        vectors = await embedder.embed([o.content for o in everything] + seeds)
        object_vectors = [l2_normalize(v) for v in vectors[: len(everything)]]
        seed_vectors = [l2_normalize(v) for v in vectors[len(everything) :]]
        for index, obj in enumerate(everything):
            best = max(cosine(object_vectors[index], s) for s in seed_vectors)
            if best > 0.18:
                scored[obj.id] = max(scored.get(obj.id, 0.0), float(best))

    ranked = sorted(scored.items(), key=lambda kv: -kv[1])
    kept = [by_id[i] for i, _ in ranked[:max_objects] if i in by_id]
    return kept, len(ranked) > max_objects


def detect_switches(objects: list[ContextObject]) -> list[Switch]:
    """Every recorded change of position within this thread, oldest first."""
    by_id = {obj.id: obj for obj in objects}
    switches: list[Switch] = []
    for obj in objects:
        if not obj.supersedes:
            continue
        previous = by_id.get(obj.supersedes)
        if previous is None:
            continue
        before_names = candidate_names(previous.content)
        after_names = candidate_names(obj.content)
        switches.append(
            Switch(
                at=_utc(obj.created_at),
                from_object_id=previous.id,
                to_object_id=obj.id,
                # The create that carried `supersedes` is the event; the object
                # id is stable and resolvable, which is what a citation needs.
                event_id=obj.id,
                before=previous.content,
                after=obj.content,
                dropped=sorted(before_names - after_names),
                adopted=sorted(after_names - before_names),
                actor="user",
                provider=str(obj.provenance.provider),
            )
        )
    switches.sort(key=lambda s: s.at)
    return switches


def _buckets(start: datetime, end: datetime, bucket: Bucket) -> list[datetime]:
    out: list[datetime] = []
    cursor = bucket.floor(start)
    while cursor <= end and len(out) < 400:
        out.append(cursor)
        cursor = (
            (cursor + timedelta(days=32)).replace(day=1)
            if bucket is Bucket.MONTH
            else cursor + bucket.delta
        )
    return out


def _mentions(name: str, text: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", text, re.I) is not None


def alternative_series(
    objects: list[ContextObject],
    switches: list[Switch],
    *,
    bucket: Bucket,
    start: datetime,
    end: datetime,
    top: int = 8,
) -> list[AlternativeSeries]:
    """Competing names, and their active mass per bucket.

    Names that appear in a switch are kept unconditionally -- they are the ones
    the user explicitly moved between, and dropping one for being rare would
    hide the very change the thread was opened to see. Everything else has to
    earn its place by appearing more than once.
    """
    switched: set[str] = set()
    for switch in switches:
        switched.update(switch.dropped)
        switched.update(switch.adopted)

    counts: Counter[str] = Counter()
    for obj in objects:
        counts.update(candidate_names(obj.content))
    # Per-statement extraction already drops a root it saw beside its compound;
    # across a thread the two can arrive in different objects, so the whole
    # vocabulary is deduped again before any of it becomes a series.
    keep = dedupe_names(set(counts) | switched)

    names = [n for n in counts if n in keep and (n in switched or counts[n] > 1)]
    names.sort(key=lambda n: (n not in switched, -counts[n], n))
    names = names[:top]

    superseded = {
        obj.supersedes: _utc(obj.created_at) for obj in objects if obj.supersedes
    }

    series: list[AlternativeSeries] = []
    for name in names:
        members = [o for o in objects if _mentions(name, o.content)]
        if not members:
            continue
        points: list[tuple[datetime, int, list[str]]] = []
        for boundary in _buckets(start, end, bucket):
            edge = min(
                end,
                (boundary + timedelta(days=32)).replace(day=1) - timedelta(microseconds=1)
                if bucket is Bucket.MONTH
                else boundary + bucket.delta - timedelta(microseconds=1),
            )
            live = [
                m.id
                for m in members
                if _utc(m.created_at) <= edge
                and not (m.retired_at and _utc(m.retired_at) <= edge)
                and not (m.id in superseded and superseded[m.id] <= edge)
            ]
            points.append((boundary, len(live), live[:10]))
        stamps = [_utc(m.created_at) for m in members]
        series.append(
            AlternativeSeries(
                name=name,
                first_seen=min(stamps),
                last_seen=max(stamps),
                total=len(members),
                points=points,
            )
        )
    return series


async def run_thread(
    store: Store,
    tenant_id: TenantId,
    *,
    subject: str = "",
    anchor_ids: list[str] | None = None,
    bucket: Bucket = Bucket.MONTH,
    window_days: int = 365,
    max_objects: int = DEFAULT_MAX_OBJECTS,
    now: datetime | None = None,
) -> ThreadReport:
    """One subject, followed through the graph."""
    end = _utc(now or datetime.now(UTC))
    objects, truncated = await gather(
        store, tenant_id, subject=subject, anchor_ids=anchor_ids, max_objects=max_objects
    )
    if objects:
        earliest = min(_utc(o.created_at) for o in objects)
        # The window follows the subject rather than the other way round: a
        # position adopted two years ago and never revisited is exactly the kind
        # of thing this view exists to surface, and a fixed 90-day window would
        # silently crop it out.
        start = min(earliest, end - timedelta(days=window_days))
    else:
        start = end - timedelta(days=window_days)

    switches = [s for s in detect_switches(objects) if s.is_substantive]
    alternatives = alternative_series(
        objects, switches, bucket=bucket, start=start, end=end
    )

    timeline = [
        {
            "at": _utc(obj.created_at).isoformat(),
            "object_id": obj.id,
            "content": obj.content,
            "kind": str(getattr(obj, "kind", "")) or str(obj.type),
            "confidence": obj.confidence,
            "provider": str(obj.provenance.provider),
            "supersedes": obj.supersedes,
            "retired": obj.retired_at is not None,
            "names": sorted(candidate_names(obj.content)),
        }
        for obj in sorted(objects, key=lambda o: _utc(o.created_at))
    ]

    return ThreadReport(
        subject=subject or "selected objects",
        object_ids=[o.id for o in objects],
        switches=switches,
        alternatives=alternatives,
        timeline=timeline,
        bucket=str(bucket),
        start=start,
        end=end,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# What can this workspace answer?
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Suggestion:
    """A question this graph has the evidence to answer, and the anchors that
    answer it.

    Derived from the graph rather than written per persona. That matters for
    more than tidiness: a hardcoded list is a demo script, and a derived one is
    a feature -- it works on a real workspace, and when it finds nothing it says
    so instead of offering four questions the graph cannot answer.
    """

    question: str
    subject: str
    anchor_ids: list[str]
    switches: int
    names: list[str]
    last_change: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "subject": self.subject,
            "anchor_ids": self.anchor_ids,
            "switches": self.switches,
            "names": self.names,
            "last_change": self.last_change.isoformat(),
        }


def _endpoints(chain: list[Switch]) -> tuple[str, str]:
    """What this chain moved *from* and *to*.

    Shared by the label and by the names shown beneath it, because they were
    computed separately and disagreed: a one-sided switch produced the question
    "How has ChatGPT → Mistral changed over time?" above a single chip reading
    "Mistral". The heading and its own evidence have to name the same things.
    """
    if not chain:
        return "", ""
    adopted = {n for sw in chain for n in sw.adopted}
    first = next((n for s in chain for n in s.dropped), "")
    if not first:
        # A one-sided switch: the new statement adopted something without the
        # old name disappearing ("Events go through Kafka; RabbitMQ is retired
        # once the cutover completes"). The position it moved *from* is still
        # the first thing the chain named, so read it off the opening statement
        # rather than labelling the thread with only its destination.
        opening = sorted(candidate_names(chain[0].before))
        first = next((n for n in opening if n not in adopted), "")
    last = next((n for s in reversed(chain) for n in s.adopted), "")
    return first, last


def _subject_for(chain: list[Switch], content: str) -> str:
    """A short label for what a chain is about.

    The endpoints, not the whole vocabulary: "Postgres 15 → Neon" is what
    changed, and it is checkable against the objects underneath. A model could
    write a nicer label ("your ledger database"), and when one is configured it
    may -- but the label has to be defensible without it, because the default
    backend is `none` and a demo is not allowed to be the only thing that works.
    """
    first, last = _endpoints(chain)
    if first and last and first != last:
        return f"{first} → {last}"
    if last or first:
        return last or first
    words = list(tokenize(content))[:4]
    return " ".join(words) if words else "this subject"


async def suggest_threads(
    store: Store, tenant_id: TenantId, *, limit: int = 6
) -> list[Suggestion]:
    """Subjects this workspace has actually recorded a change of position on.

    Every suggestion is backed by at least one substantive switch, so clicking
    one cannot land on an empty chart. That is the whole selection rule: offer
    the questions the evidence supports, and nothing else.
    """
    objects = [
        obj
        for obj in await store.list_objects(
            tenant_id, limit=4000, include_retired=True, include_superseded=True
        )
        if obj.type not in _EXCLUDED and obj.content.strip()
    ]
    switches = [s for s in detect_switches(objects) if s.is_substantive]
    if not switches:
        return []

    by_id = {obj.id: obj for obj in objects}
    # Group switches into chains, so "Postgres 15 → 16 → Neon" is one subject
    # with two switches rather than two subjects with one each.
    root_of: dict[str, str] = {}
    for obj in objects:
        cursor, seen = obj.id, set()
        while (nxt := by_id.get(cursor)) and nxt.supersedes and cursor not in seen:
            seen.add(cursor)
            cursor = nxt.supersedes
        root_of[obj.id] = cursor

    chains: dict[str, list[Switch]] = {}
    for switch in switches:
        chains.setdefault(root_of.get(switch.to_object_id, switch.to_object_id), []).append(switch)

    suggestions: list[Suggestion] = []
    for root, chain in chains.items():
        chain.sort(key=lambda s: s.at)
        # Lead with the endpoints the question names, so the chips underneath
        # are the evidence for the heading rather than a different list.
        opening, destination = _endpoints(chain)
        names: list[str] = [n for n in (opening, destination) if n]
        for switch in chain:
            for name in switch.dropped + switch.adopted:
                if name not in names:
                    names.append(name)
        members = [root] + [s.to_object_id for s in chain]
        latest = by_id.get(chain[-1].to_object_id)
        subject = _subject_for(chain, latest.content if latest else "")
        suggestions.append(
            Suggestion(
                question=f"How has {subject} changed over time?",
                subject=subject,
                anchor_ids=[m for m in members if m in by_id],
                switches=len(chain),
                names=names[:6],
                last_change=chain[-1].at,
            )
        )

    # Swaps first. A chain where one name left *and* another arrived is a
    # choice being changed; a chain where a name only arrived is usually a fact
    # being refined, and a correction with no names either side is a rewording.
    # Without this the lawyer persona led with "how has Gerald Roe changed over
    # time" -- a real recorded correction, and not a question anyone would ask.
    def rank(suggestion: Suggestion) -> tuple[bool, int, float]:
        is_swap = "→" in suggestion.subject
        return (not is_swap, -suggestion.switches, -suggestion.last_change.timestamp())

    suggestions.sort(key=rank)
    return suggestions[:limit]
