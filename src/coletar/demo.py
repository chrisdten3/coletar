"""Demo workspaces: three professions, three graphs that behave like real ones.

Separate from `seed.py`, which is the *fixture* corpus — one object of every type
plus a supersedes chain, sized for a test to assert against. This is for showing the
product to a person, and the two want opposite things. A fixture wants to be minimal
and stable. A demo wants to be dense enough that the Library groups into something,
the Atlas has hubs worth clicking, the review queue has a backlog, and the History
page has a fact that visibly changed its mind.

**Everything here is invented.** No client, deal, matter or person named below is
real, and each persona's workspace is built from the same public product story rather
than from anyone's data. That matters for more than politeness: two of these
professions handle material that is genuinely privileged or price-sensitive, and the
demo is the wrong place to find out that someone pasted the real thing in.

Why these three, and not three copies of one persona with the nouns changed:

  * **The engineer** is the case where portability is the whole pitch. Her graph is
    full of decisions that were reached in one assistant and are needed in another.
  * **The lawyer** is the case for `Locality`. Privilege is not a confidence score
    or a sensitivity label — it is a hard statement about which surfaces may ever
    see a thing, which is exactly what `local_only` encodes and what no
    memory feature that syncs everything can express.
  * **The banker** is the case for both at once, plus time. Material non-public
    information is restricted *and* expires: a name comes off the restricted list
    when a deal is announced, which is `valid_from`/`valid_until` doing real work
    rather than demonstrating a field.

Each graph deliberately contains the awkward states, because a demo that only shows
the happy path is a demo that answers no questions:

  * a **supersedes chain**, so History has something to diff
  * a **contradicts** pair, so the conflict resolution UI has a conflict
  * **unreviewed** objects, so the review gate is visibly a gate
  * **pending episodes**, so the capture queue is not empty
  * a spread of `extraction_method` and `confidence`, so the Context Inspector is
    explaining real differences rather than one value repeated
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from coletar.capture import capture_turn
from coletar.retrieval.trace import ComponentVersions, RetrievalTrace, query_digest
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    GLOBAL_SCOPE,
    ContextObject,
    Edge,
    EdgeType,
    ExtractionMethod,
    Locality,
    LocalityMode,
    Memory,
    MemoryKind,
    ObjectType,
    OriginType,
    Provenance,
    Provider,
    Scope,
    ScopeType,
    Sensitivity,
)
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store


#: Anchors every relative date in this module. Passed in rather than read from the
#: clock inside the builders so that one demo run produces one coherent timeline --
#: a graph whose "three days ago" objects were written across a midnight boundary
#: tells a slightly different story on each page.
def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class DemoResult:
    """What was built, by role, so a caller can report or assert on it."""

    tenant_id: str
    objects: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def id(self, role: str) -> str:
        return self.objects[role]


class Workspace:
    """A small builder over `Store`, so a persona reads as a list of facts.

    Every write goes through `store.put_object`, which appends its own event -- the
    append-only log is the provenance record and a demo that bypassed it would be
    showing a workspace that could not explain itself, which is the one thing the
    Context Inspector exists to do.
    """

    def __init__(self, store: Store, tenant_id: TenantId, *, now: datetime) -> None:
        self._store = store
        self._tenant = tenant_id
        self._now = now
        self.result = DemoResult(tenant_id=str(tenant_id))

    def days_ago(self, days: float) -> datetime:
        return self._now - timedelta(days=days)

    async def _put(
        self,
        role: str,
        obj: ContextObject,
        *,
        actor: Actor = Actor.USER,
        event_type: EventType | None = None,
        at: datetime | None = None,
        detail: dict[str, Any] | None = None,
    ) -> ContextObject:
        # The event is dated to the object, not to the seed run. Without this
        # every demo workspace has a graph spread over months and a log spread
        # over four seconds, so any question about history answers "it all
        # happened just now" -- which is the one answer that makes an
        # observability surface useless. `put_object` fills before/after.
        stored = await self._store.put_object(
            self._tenant,
            obj,
            event=Event(
                type=event_type
                or (EventType.OBJECT_CREATED if obj.version == 1 else EventType.OBJECT_UPDATED),
                object_id=obj.id,
                actor=actor,
                provider=obj.provenance.provider,
                at=at or obj.updated_at,
                detail={"type": str(obj.type), "scope": str(obj.scope), **(detail or {})},
            ),
        )
        self.result.objects[role] = stored.id
        kind = str(obj.type)
        self.result.counts[kind] = self.result.counts.get(kind, 0) + 1
        return stored

    async def node(
        self,
        role: str,
        *,
        type: ObjectType,
        content: str,
        scope: Scope = GLOBAL_SCOPE,
        locality: Locality | None = None,
        sensitivity: Sensitivity = Sensitivity.NORMAL,
        method: ExtractionMethod = ExtractionMethod.EXPLICIT_STATEMENT,
        origin: OriginType = OriginType.USER,
        provider: Provider = Provider.COLETAR,
        confidence: float = 0.92,
        days_ago: float = 30,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ContextObject:
        """Any non-Memory object: project, conversation, decision, artifact, entity, fact."""
        created = self.days_ago(days_ago)
        return await self._put(
            role,
            ContextObject(
                type=type,
                content=content,
                scope=scope,
                locality=locality or Locality(),
                sensitivity=sensitivity,
                confidence=confidence,
                extraction_method=method,
                created_at=created,
                updated_at=created,
                valid_from=valid_from,
                valid_until=valid_until,
                payload=payload or {},
                provenance=Provenance(
                    origin_type=origin,
                    provider=provider,
                    confidence=confidence,
                    captured_at=created,
                ),
            ),
        )

    async def memory(
        self,
        role: str,
        content: str,
        *,
        kind: MemoryKind = MemoryKind.FACT,
        scope: Scope = GLOBAL_SCOPE,
        locality: Locality | None = None,
        sensitivity: Sensitivity = Sensitivity.NORMAL,
        method: ExtractionMethod = ExtractionMethod.EXPLICIT_STATEMENT,
        origin: OriginType = OriginType.USER,
        provider: Provider = Provider.COLETAR,
        confidence: float | None = None,
        supersedes: str | None = None,
        days_ago: float = 20,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
    ) -> ContextObject:
        created = self.days_ago(days_ago)
        memory = Memory.from_write(
            content,
            kind=kind,
            scope=scope,
            locality=locality or Locality(),
            sensitivity=sensitivity,
            extraction_method=method,
            origin_type=origin,
            provider=provider,
            confidence=confidence,
            supersedes=supersedes,
        )
        # Set after construction because `from_write` stamps "now": a demo whose
        # every object was created in the same second has no history to show.
        memory.created_at = created
        memory.updated_at = created
        memory.valid_from = valid_from
        memory.valid_until = valid_until
        memory.provenance = memory.provenance.model_copy(update={"captured_at": created})
        return await self._put(role, memory)

    async def episode(
        self,
        role: str,
        text: str,
        *,
        surface: Provider,
        scope: Scope = GLOBAL_SCOPE,
        pending: bool = True,
        speaker: str = "user",
        conversation_id: str | None = None,
    ) -> ContextObject:
        """A captured turn, through the real capture path.

        Deliberately not hand-built: `capture_turn` encrypts the text under a
        per-object key, stores the key, stamps the pending flag and sets the TTL. A
        demo episode written directly as plaintext would fail to decrypt in the
        workspace, which is how you learn that the demo was not using the pipeline.
        """
        episode = await capture_turn(
            self._store,
            self._tenant,
            text,
            surface=surface,
            scope=scope,
            role=speaker,  # type: ignore[arg-type]
            conversation_id=conversation_id,
        )
        if not pending:
            # Already mined. The batch pass clears the flag; doing it here keeps the
            # capture queue from claiming a backlog the graph does not have.
            episode.payload = {**episode.payload, "needs_model_extraction": False}
            episode = await self._store.put_object(self._tenant, episode)
        self.result.objects[role] = episode.id
        self.result.counts["episode"] = self.result.counts.get("episode", 0) + 1
        return episode

    async def link(
        self, src: str, dst: str, type: EdgeType, *, confidence: float = 1.0
    ) -> None:
        """An edge between two roles already built. Roles, not ids, so a persona
        never has to hold an id in a local variable to draw an edge."""
        await self._store.add_edge(
            self._tenant,
            Edge(
                src_id=self.result.objects[src],
                dst_id=self.result.objects[dst],
                type=type,
                confidence=confidence,
            ),
        )
        self.result.counts["edge"] = self.result.counts.get("edge", 0) + 1

    async def mentions(self, entity_role: str, *fact_roles: str) -> None:
        """`mentions` from each fact to one entity. The Atlas is built from these,
        so an entity with no incoming mentions is an entity nobody can reach."""
        for role in fact_roles:
            await self.link(role, entity_role, EdgeType.MENTIONS)

    async def belongs(self, project_role: str, *roles: str) -> None:
        for role in roles:
            await self.link(role, project_role, EdgeType.BELONGS_TO)

    # -- History. ----------------------------------------------------------
    #
    # Everything below exists so the observability surface has something to
    # observe. A persona built only from `node` and `memory` is a graph with no
    # past: every object appears at once, nothing is ever read, and every chart
    # of it is one spike on the day the seed ran. These write the *rest* of a
    # workspace's life -- the reads, the confirmations, the corrections, the
    # reach changes -- at the times they would have happened.

    async def current(self, role: str) -> ContextObject | None:
        """The stored object behind a role, for a generator that needs to read
        what it is about to correct."""
        object_id = self.result.objects.get(role)
        if object_id is None:
            return None
        return await self._store.get_object(self._tenant, object_id)

    async def mark(
        self,
        role: str,
        event_type: EventType,
        *,
        days_ago: float,
        actor: Actor = Actor.USER,
        provider: Provider = Provider.COLETAR,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """A non-revision event: it happened *to* the object without changing it.

        Review and corroboration deliberately do not touch the row (§2, and
        `REVISION_EVENTS`): looking at a fact, or hearing it again, is a fact
        about its history rather than a new version of it.
        """
        await self._store.append_event(
            self._tenant,
            Event(
                type=event_type,
                object_id=self.result.objects[role],
                actor=actor,
                provider=provider,
                at=self.days_ago(days_ago),
                detail=detail or {},
            ),
        )
        key = str(event_type)
        self.result.counts[key] = self.result.counts.get(key, 0) + 1

    async def review(self, *roles: str, days_ago: float = 1) -> None:
        for offset, role in enumerate(roles):
            # Spread across the sitting, so the review feed is a session rather
            # than a single timestamp repeated.
            await self.mark(
                role,
                EventType.OBJECT_REVIEWED,
                days_ago=days_ago - offset * 0.004,
                actor=Actor.USER,
            )

    async def corroborate(
        self, role: str, *, days_ago: float, provider: Provider, note: str = ""
    ) -> None:
        """The same fact, heard again on another surface. Raises no version and
        writes no row; it is the graph's evidence that a memory is still true."""
        await self.mark(
            role,
            EventType.OBJECT_CORROBORATED,
            days_ago=days_ago,
            actor=Actor.MODEL,
            provider=provider,
            detail={"surface": str(provider), "note": note} if note else {"surface": str(provider)},
        )

    async def rescope(
        self, role: str, *, days_ago: float, locality: Locality, reason: str
    ) -> None:
        """A reach change, as its own event type, because "who may read this"
        changing is the one edit a user is most likely to be asked to justify."""
        object_id = self.result.objects[role]
        current = await self._store.get_object(self._tenant, object_id)
        if current is None:
            return
        before = str(current.locality.mode)
        current.locality = locality
        await self._put(
            role,
            current,
            actor=Actor.USER,
            event_type=EventType.OBJECT_RESCOPED,
            at=self.days_ago(days_ago),
            detail={
                "field": "locality",
                "from": before,
                "to": str(locality.mode),
                "reason": reason,
            },
        )

    async def retire(self, role: str, *, days_ago: float, reason: str) -> None:
        """Retired, never deleted (constraint #6). The row stays readable and the
        chart it appears in keeps its shape before the retirement date."""
        object_id = self.result.objects[role]
        current = await self._store.get_object(self._tenant, object_id)
        if current is None:
            return
        when = self.days_ago(days_ago)
        current.retired_at = when
        await self._put(
            role,
            current,
            actor=Actor.USER,
            event_type=EventType.OBJECT_RETIRED,
            at=when,
            detail={"reason": reason},
        )

    async def trace(
        self,
        *,
        days_ago: float,
        surface: str,
        provider: Provider,
        roles: list[str],
        query: str,
        record_query_text: bool = False,
        withheld: int = 0,
        principal: str | None = None,
        latency_ms: float = 42.0,
    ) -> None:
        """One recorded read.

        Built through `RetrievalTrace.as_detail` rather than as a hand-written
        dict so a seeded trace and a real one are the same shape. A demo whose
        telemetry has a field the product does not write is a demo that will
        show a column the real workspace leaves blank.
        """
        returned = [self.result.objects[r] for r in roles if r in self.result.objects]
        detail = RetrievalTrace(
            query_digest=query_digest(query),
            scope="any",
            surface=surface,
            provider=str(provider),
            principal=principal,
            top_k=12,
            token_budget=2000,
            versions=ComponentVersions(
                embedder=self._store.embedder_model, backend="demo"
            ),
            returned_ids=returned,
            token_estimate=sum(40 for _ in returned),
            stage_ms={"narrow": round(latency_ms * 0.3, 1), "rank": round(latency_ms * 0.7, 1)},
            # §11: the text is recorded only when the caller opted in, per call.
            # Most demo traces show only a digest, because most real ones do.
            query_text=query if record_query_text else None,
        ).as_detail()
        if withheld:
            detail["withheld"] = withheld
        await self._store.append_event(
            self._tenant,
            Event(
                type=EventType.RETRIEVAL_TRACE,
                actor=Actor.CONNECTOR,
                provider=provider,
                at=self.days_ago(days_ago),
                detail=detail,
            ),
        )
        self.result.counts["retrieval.trace"] = self.result.counts.get("retrieval.trace", 0) + 1


@dataclass(frozen=True)
class Persona:
    """One demo account: who they are, and how to build their graph."""

    key: str
    name: str
    email: str
    title: str
    #: One line for the operator running the seed, and for the demo script.
    headline: str
    build: Callable[[Workspace], Awaitable[None]]


def _project(pid: str) -> Scope:
    return Scope(type=ScopeType.PROJECT, id=pid)


def _only(*surfaces: Provider) -> Locality:
    """Readable by these surfaces and no others. The product's central claim (§10)
    is that context travels; this is the per-object opt-out from that."""
    return Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset(surfaces))


# ---------------------------------------------------------------------------
# Traffic and backfill.
#
# A persona's hand-written facts are the interesting part of a demo graph and
# about two percent of a real one. The rest is volume: months of ordinary
# writes, and the reads that make those writes worth keeping. These generate
# that volume deterministically, because a demo whose charts change shape on
# every seed run cannot be demoed twice.
# ---------------------------------------------------------------------------


def _rng_for(key: str) -> random.Random:
    """A stable stream per persona. Seeded from the name rather than a literal
    so adding a persona never reshuffles an existing one's history."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _pick(rng: random.Random, weights: dict[Any, float]) -> Any:
    total = sum(weights.values())
    cut = rng.random() * total
    for value, weight in weights.items():
        cut -= weight
        if cut <= 0:
            return value
    return next(iter(weights))


@dataclass(frozen=True)
class Topic:
    """One thread of a working life, as the sentences it produces.

    Grouped by topic on purpose: the idea-clustering view charts the mass of a
    theme over time, and it can only be shown working against a graph whose
    themes actually rise and fall. `weight_curve` is what makes them do that.
    """

    label: str
    scope: Scope
    sentences: list[str]
    kinds: list[MemoryKind]
    #: Relative activity at the start and end of the window. (1.0, 0.1) is a
    #: project winding down; (0.1, 1.0) is one ramping up.
    weight_curve: tuple[float, float] = (1.0, 1.0)
    locality: Locality | None = None
    sensitivity: Sensitivity = Sensitivity.NORMAL


@dataclass(frozen=True)
class TrafficProfile:
    """How one persona's workspace is written to and read from over time."""

    key: str
    days: int
    topics: list[Topic]
    #: Which assistant asks, and how often. The whole point of a portable graph
    #: is that this is a distribution rather than a constant.
    providers: dict[Provider, float]
    surfaces: dict[str, float]
    #: Mean writes and reads on a working day, before seasonality.
    writes_per_day: float = 1.6
    reads_per_day: float = 7.0
    #: Share of traces whose caller opted into recording the query text (§11).
    query_text_share: float = 0.35
    questions: list[str] = field(default_factory=list)


def _seasonal(rng: random.Random, day: int, days: int, base: float) -> int:
    """Events on one day: fewer at weekends, more as the workspace matures."""
    when = day
    weekday = (when % 7) < 5
    ramp = 0.45 + 0.55 * (1.0 - day / max(days, 1))
    mean = base * ramp * (1.0 if weekday else 0.22)
    # Poisson via inversion; small means, so the loop is short.
    limit, total, probability = 0, rng.random(), 2.718281828 ** (-mean)
    cumulative = probability
    while total > cumulative and limit < 20:
        limit += 1
        probability *= mean / limit
        cumulative += probability
    return limit


def _hour_jitter(rng: random.Random) -> float:
    """A fraction of a day inside working hours, so timestamps do not all land
    at midnight and the day/week buckets are not suspiciously clean."""
    return rng.uniform(8.0, 19.0) / 24.0


async def backfill_topics(w: Workspace, profile: TrafficProfile) -> list[str]:
    """Write each topic's sentences across the window, following its curve.

    Returns the roles created, so the traffic pass has something to return from
    searches. Confidence and extraction method vary because the Context
    Inspector's job is to explain differences, and a graph where every object
    scored 0.92 gives it nothing to explain.
    """
    rng = _rng_for(profile.key + ":writes")
    roles: list[str] = []
    counter = 0

    for topic in profile.topics:
        start_weight, end_weight = topic.weight_curve
        for index, sentence in enumerate(topic.sentences):
            # Position within the topic's own run, mapped onto the window.
            position = index / max(len(topic.sentences) - 1, 1)
            weight = start_weight + (end_weight - start_weight) * position
            # A low-weight stretch produces sparser writes: skip some of them
            # rather than compressing, so the quiet period shows up on the chart
            # as a gap instead of the same run of writes squeezed together.
            if rng.random() > min(1.0, 0.4 + 0.6 * weight):
                continue
            day = profile.days * (1.0 - position * rng.uniform(0.82, 1.0))
            method = _pick(
                rng,
                {
                    ExtractionMethod.EXPLICIT_STATEMENT: 5.0,
                    ExtractionMethod.ACCOUNT_EXPORT_PARSE: 2.0,
                    ExtractionMethod.PROVIDER_CURATED: 1.2,
                },
            )
            provider = _pick(rng, profile.providers)
            # Mined prose is the less certain pile; `default_confidence`
            # already scores that method lower, and the spread here is what
            # gives the Context Inspector a difference to explain.
            centre = 0.78 if method is ExtractionMethod.ACCOUNT_EXPORT_PARSE else 0.9
            confidence = round(min(0.99, max(0.34, rng.gauss(centre, 0.12))), 2)
            counter += 1
            role = f"bf_{profile.key}_{counter:03d}"
            await w.memory(
                role,
                sentence,
                kind=rng.choice(topic.kinds),
                scope=topic.scope,
                locality=topic.locality,
                sensitivity=topic.sensitivity,
                method=method,
                origin=OriginType.USER
                if method is ExtractionMethod.EXPLICIT_STATEMENT
                else OriginType.AGENT,
                provider=provider,
                confidence=confidence,
                days_ago=max(0.5, day - _hour_jitter(rng)),
            )
            roles.append(role)

    return roles


def _fragment(rng: random.Random, sentence: str) -> str:
    """A sentence as export mining would actually have recovered it.

    On a real 17,881-turn export the mined half was 42% fragments under 45
    characters against a median of 125 for provider-curated memories. A demo
    whose imported memories are as clean as its explicit ones hides the single
    most important thing the Context Inspector has to show a user, which is that
    those two piles are not the same quality of fact.
    """
    words = sentence.rstrip(".").split()
    if len(words) > 7 and rng.random() < 0.45:
        cut = rng.randint(3, max(4, len(words) // 2))
        return " ".join(words[:cut])
    return sentence.rstrip(".")


async def import_burst(w: Workspace, profile: TrafficProfile) -> list[str]:
    """The day the user imported their provider export.

    One acquisition event, hundreds of objects, all stamped
    `ACCOUNT_EXPORT_PARSE` at a visibly lower confidence -- which is what the
    real import flow produces (§8.1, §11: the user clicks their own export
    button and the file lands). It is the spike every `extraction_method` chart
    in this product should open with, and without it the method breakdown is a
    flat line that explains nothing.
    """
    rng = _rng_for(profile.key + ":import")
    # Late in the window, because importing is what someone does on their first
    # day -- so it sits near the oldest end of the history.
    landed = profile.days * rng.uniform(0.86, 0.94)
    provider = _pick(rng, {Provider.CHATGPT: 3.0, Provider.CLAUDE: 2.0})

    roles: list[str] = []
    counter = 0
    for topic in profile.topics:
        for sentence in topic.sentences:
            if rng.random() > 0.62:
                continue
            counter += 1
            role = f"imp_{profile.key}_{counter:03d}"
            text = _fragment(rng, sentence)
            await w.memory(
                role,
                text,
                kind=rng.choice([MemoryKind.FACT, MemoryKind.FACT, MemoryKind.INFERENCE]),
                scope=topic.scope,
                locality=topic.locality,
                sensitivity=topic.sensitivity,
                method=ExtractionMethod.ACCOUNT_EXPORT_PARSE,
                origin=OriginType.AGENT,
                provider=provider,
                # Mined prose is the low-confidence pile by construction, and
                # `default_confidence` already scores this method at 0.60.
                confidence=round(min(0.86, max(0.32, rng.gauss(0.58, 0.13))), 2),
                # The whole archive lands within one sitting, not over months.
                days_ago=max(0.5, landed - rng.uniform(0.0, 0.35)),
            )
            roles.append(role)
    return roles


async def simulate_traffic(
    w: Workspace, profile: TrafficProfile, roles: list[str]
) -> None:
    """Months of reads, reviews and confirmations over an existing graph.

    Reads are the telemetry half of the thesis: "which assistant has seen this
    fact" is only answerable because every retrieval appends a trace, and a demo
    with no traces shows that table empty and the claim unmade.
    """
    if not roles:
        return
    rng = _rng_for(profile.key + ":reads")
    questions = profile.questions or ["what do you know about my work"]

    for day in range(profile.days):
        for _ in range(_seasonal(rng, day, profile.days, profile.reads_per_day)):
            # A search returns a few related objects, not a random scatter: the
            # neighbourhood is what a vector index would have given back.
            anchor = rng.randrange(len(roles))
            span = rng.randint(1, 5)
            hit = [roles[i] for i in range(anchor, min(anchor + span, len(roles)))]
            provider = _pick(rng, profile.providers)
            await w.trace(
                days_ago=max(0.02, day + _hour_jitter(rng)),
                surface=_pick(rng, profile.surfaces),
                provider=provider,
                roles=hit,
                query=rng.choice(questions),
                record_query_text=rng.random() < profile.query_text_share,
                principal=f"key_{str(provider)[:6]}",
                latency_ms=round(rng.gauss(48, 14), 1),
            )

        # A search that matched only restricted context returns nothing and
        # still happened. The table shows it as an attempt, because "no rows"
        # and "reach said no" are different facts about the same graph.
        if rng.random() < 0.05:
            await w.trace(
                days_ago=max(0.02, day + _hour_jitter(rng)),
                surface=_pick(rng, profile.surfaces),
                provider=_pick(rng, profile.providers),
                roles=[],
                query=rng.choice(questions),
                withheld=rng.randint(1, 3),
            )

    # Confirmations: the same fact heard again on another surface.
    for role in rng.sample(roles, k=min(len(roles), max(4, len(roles) // 6))):
        await w.corroborate(
            role,
            days_ago=rng.uniform(1, profile.days * 0.7),
            provider=_pick(rng, profile.providers),
            note="repeated on another surface",
        )

    # Review is a gate the user walks through in sittings, not continuously.
    reviewable = [r for r in roles if rng.random() < 0.72]
    for index, role in enumerate(reviewable):
        through = 1 - index / max(len(reviewable), 1)
        await w.review(role, days_ago=max(0.4, profile.days * through * 0.9))

    # Corrections. A graph that never contradicts itself is a graph nobody is
    # using, and the supersession rate is one of the few honest signals of
    # whether extraction is drifting -- so the demo has to have some.
    generated = [r for r in roles if r.startswith("bf_")]
    for role in rng.sample(generated, k=min(len(generated), max(3, len(generated) // 9))):
        original = w.result.objects.get(role)
        if original is None:
            continue
        source = await w.current(role)
        if source is None or source.supersedes is not None:
            continue
        when = rng.uniform(1.5, profile.days * 0.55)
        await w.memory(
            f"{role}_fix",
            _revise_sentence(rng, source.content),
            kind=MemoryKind.CORRECTION,
            scope=source.scope,
            locality=source.locality if source.locality.mode is LocalityMode.LOCAL_ONLY else None,
            sensitivity=source.sensitivity,
            method=ExtractionMethod.EXPLICIT_STATEMENT,
            origin=OriginType.USER,
            provider=_pick(rng, profile.providers),
            confidence=round(min(0.99, source.confidence + rng.uniform(0.03, 0.12)), 2),
            supersedes=original,
            days_ago=when,
        )

    # Retirements: context that stopped being true rather than being wrong.
    for role in rng.sample(generated, k=min(len(generated), max(2, len(generated) // 16))):
        if role.endswith("_fix"):
            continue
        await w.retire(
            role,
            days_ago=rng.uniform(1.0, profile.days * 0.4),
            reason="no longer relevant to an active project",
        )

    # Reach changes: the edit a user is most likely to be asked to justify.
    for role in rng.sample(generated, k=min(len(generated), max(2, len(generated) // 20))):
        await w.rescope(
            role,
            days_ago=rng.uniform(1.0, profile.days * 0.6),
            locality=_only(Provider.LOCAL),
            reason="held back from hosted assistants after review",
        )


#: Small, deterministic edits that read like a real correction rather than a
#: string with "(updated)" appended. Keyed on what the sentence contains, so a
#: correction lands on something the sentence actually claimed.
_REVISIONS: tuple[tuple[str, str], ...] = (
    ("two weeks", "three weeks"),
    ("15 minutes", "10 minutes"),
    ("hourly", "every 30 minutes"),
    ("60 minutes", "75 minutes"),
    ("three hours", "two hours"),
    ("two working days", "one working day"),
    ("Postgres 16", "Postgres 17"),
    ("30 days", "45 days"),
    ("Thursdays", "Tuesdays"),
    ("two years", "three years"),
    ("November", "January"),
    ("three levels", "two levels"),
)


def _revise_sentence(rng: random.Random, content: str) -> str:
    for old, new in _REVISIONS:
        if old in content:
            return content.replace(old, new)
    # Nothing numeric to move, so the correction narrows the claim instead --
    # which is what most real corrections do.
    return f"{content.rstrip('.')}, as of the most recent review."


# ---------------------------------------------------------------------------
# 1. The software engineer.
#
# The portability case. Maya's decisions are reached in whichever assistant she had
# open, and every one of them is needed in the others -- which is the situation a
# per-product memory feature cannot represent, because each product only remembers
# what was said to it.
# ---------------------------------------------------------------------------


async def _build_engineer(w: Workspace) -> None:
    ledger = _project("proj_ledger_rewrite")
    oncall = _project("proj_oncall")
    hiring = _project("proj_hiring")

    await w.node(
        "p_ledger",
        type=ObjectType.PROJECT,
        content=(
            "Ledger rewrite — moving Northwind's payments ledger off the Rails "
            "monolith onto an event-sourced service in Go. Target: cut settlement "
            "reconciliation from 40 minutes to under 5."
        ),
        scope=ledger,
        days_ago=180,
    )
    await w.node(
        "p_oncall",
        type=ObjectType.PROJECT,
        content=(
            "On-call health — reducing pages on the payments rotation. Started after "
            "the rotation averaged 11 pages a week for a quarter."
        ),
        scope=oncall,
        days_ago=95,
    )
    await w.node(
        "p_hiring",
        type=ObjectType.PROJECT,
        content="Hiring — two backend openings on the payments platform team, Q4.",
        scope=hiring,
        days_ago=60,
    )

    # --- Preferences and instructions. The things she is tired of restating. ----
    await w.memory(
        "pref_money",
        "Money is always integer minor units in our services. Never float, never "
        "Decimal at the storage boundary — Decimal only inside a single calculation.",
        kind=MemoryKind.PREFERENCE,
        days_ago=170,
        confidence=0.97,
    )
    await w.memory(
        "pref_tests",
        "Show me the failing test output before you propose a fix. A diagnosis I "
        "can't check against a real failure is a guess.",
        kind=MemoryKind.INSTRUCTION,
        method=ExtractionMethod.MCP_LIVE_WRITE,
        provider=Provider.CLAUDE,
        days_ago=150,
    )
    await w.memory(
        "pref_comments",
        "Comments should explain why, not what. If a comment restates the line "
        "below it, delete the comment.",
        kind=MemoryKind.PREFERENCE,
        method=ExtractionMethod.PROVIDER_CURATED,
        provider=Provider.CLAUDE,
        days_ago=140,
    )
    await w.memory(
        "pref_go",
        "In Go, return concrete error types and wrap with %w. No sentinel string "
        "comparison, and no naked panics outside main.",
        kind=MemoryKind.PREFERENCE,
        scope=ledger,
        method=ExtractionMethod.MODEL_EXTRACTED,
        provider=Provider.CHATGPT,
        origin=OriginType.AGENT,
        days_ago=120,
    )
    await w.memory(
        "pref_review",
        "I review PRs by reading the tests first. Put the test diff at the top of "
        "any summary you write for me.",
        kind=MemoryKind.PREFERENCE,
        method=ExtractionMethod.BROWSER_CAPTURE,
        provider=Provider.CHATGPT,
        origin=OriginType.AGENT,
        days_ago=44,
    )

    # --- Decisions, with the conversations they came out of. -------------------
    await w.node(
        "conv_events",
        type=ObjectType.CONVERSATION,
        content=(
            "Long session working out whether the ledger's event store should keep "
            "one stream per account or one global stream with account projections."
        ),
        scope=ledger,
        origin=OriginType.AGENT,
        provider=Provider.CLAUDE,
        method=ExtractionMethod.BROWSER_CAPTURE,
        days_ago=136,
    )
    await w.node(
        "dec_streams",
        type=ObjectType.DECISION,
        content=(
            "One event stream per account, with a global projection rebuilt "
            "asynchronously. Per-account streams keep the write path's concurrency "
            "control to a single-key optimistic check; a global stream would need a "
            "sequencer we would then have to make highly available."
        ),
        scope=ledger,
        days_ago=135,
        confidence=0.95,
    )
    await w.node(
        "dec_outbox",
        type=ObjectType.DECISION,
        content=(
            "Settlement notifications go through a transactional outbox, not a "
            "direct publish. We accept the extra table because a payment that "
            "settled and did not notify is a support ticket, and a notification "
            "for a payment that rolled back is a refund."
        ),
        scope=ledger,
        days_ago=98,
        confidence=0.95,
    )
    await w.node(
        "dec_no_k8s",
        type=ObjectType.DECISION,
        content=(
            "Ledger runs on ECS Fargate, not the shared Kubernetes cluster. Two "
            "engineers cannot carry cluster upgrades on top of a rewrite, and the "
            "workload is four stateless services and a queue."
        ),
        scope=ledger,
        days_ago=77,
        confidence=0.9,
    )
    await w.node(
        "art_adr",
        type=ObjectType.ARTIFACT,
        content="docs/adr/0007-per-account-event-streams.md — the ADR for the stream decision.",
        scope=ledger,
        origin=OriginType.AGENT,
        provider=Provider.CLAUDE,
        days_ago=134,
    )
    await w.node(
        "art_runbook",
        type=ObjectType.ARTIFACT,
        content=(
            "runbooks/settlement-stuck.md — what to do when the settlement worker's "
            "lag alarm fires. Written after the third time it fired at 3am."
        ),
        scope=oncall,
        origin=OriginType.AGENT,
        provider=Provider.CHATGPT,
        days_ago=70,
    )

    # --- The supersedes chain. A date that moved twice, which is what History is for.
    v1 = await w.memory(
        "cutover_v1",
        "Ledger cutover is planned for 14 October.",
        days_ago=90,
        confidence=0.9,
    )
    v2 = await w.memory(
        "cutover_v2",
        "Ledger cutover moved to 4 November — the reconciliation backfill needs a "
        "full month of parallel running, not two weeks.",
        kind=MemoryKind.CORRECTION,
        supersedes=v1.id,
        days_ago=58,
        method=ExtractionMethod.MCP_LIVE_WRITE,
        provider=Provider.CLAUDE,
    )
    await w.memory(
        "cutover_v3",
        "Ledger cutover is 18 November. Compliance asked for a two-week freeze "
        "either side of the November board meeting.",
        kind=MemoryKind.CORRECTION,
        supersedes=v2.id,
        days_ago=16,
        method=ExtractionMethod.EXPLICIT_STATEMENT,
        confidence=0.96,
    )

    # --- The conflict. Two live objects that cannot both be true. --------------
    await w.memory(
        "pg_version",
        "Ledger's Postgres is 15, so we can't use MERGE in the reconciliation query.",
        scope=ledger,
        method=ExtractionMethod.MODEL_EXTRACTED,
        provider=Provider.CHATGPT,
        origin=OriginType.AGENT,
        confidence=0.72,
        days_ago=52,
    )
    await w.memory(
        "pg_version_other",
        "Ledger's Postgres was upgraded to 16 in the August maintenance window.",
        scope=ledger,
        method=ExtractionMethod.BROWSER_CAPTURE,
        provider=Provider.CLAUDE,
        origin=OriginType.AGENT,
        confidence=0.74,
        days_ago=30,
    )
    await w.link("pg_version_other", "pg_version", EdgeType.CONTRADICTS, confidence=0.86)

    # --- Something she keeps off the hosted assistants. ------------------------
    await w.memory(
        "comp_band",
        "My current band is L5 and I'm pushing for L6 at the spring calibration. "
        "Don't reference compensation in anything I might paste into a shared doc.",
        kind=MemoryKind.INSTRUCTION,
        sensitivity=Sensitivity.PERSONAL,
        locality=_only(Provider.LOCAL),
        days_ago=40,
        confidence=0.95,
    )
    await w.memory(
        "hiring_note",
        "For the backend openings: I screen for people who can explain a rollback, "
        "not people who can invert a tree. Ask candidates about the worst outage "
        "they were part of.",
        kind=MemoryKind.PREFERENCE,
        scope=hiring,
        locality=_only(Provider.LOCAL, Provider.CLAUDE),
        days_ago=35,
    )

    # --- Goals. ---------------------------------------------------------------
    await w.memory(
        "goal_cutover",
        "Get the ledger cutover done before the end of the year without a Sev-1.",
        kind=MemoryKind.GOAL,
        scope=ledger,
        days_ago=57,
    )
    await w.memory(
        "goal_pages",
        "Get the payments rotation under 4 pages a week by the end of Q4.",
        kind=MemoryKind.GOAL,
        scope=oncall,
        days_ago=94,
    )
    await w.memory(
        "goal_promo",
        "Make the case for L6 with the ledger rewrite as the anchor project.",
        kind=MemoryKind.GOAL,
        sensitivity=Sensitivity.PERSONAL,
        locality=_only(Provider.LOCAL),
        days_ago=39,
    )

    # --- Inferences, which are the low-confidence end of the spread. ----------
    await w.memory(
        "inf_hours",
        "Maya does her deep work in the morning and schedules meetings after 2pm.",
        kind=MemoryKind.INFERENCE,
        method=ExtractionMethod.DERIVED_SUMMARY,
        origin=OriginType.AGENT,
        provider=Provider.COLETAR,
        days_ago=25,
    )
    await w.memory(
        "inf_style",
        "Prefers to be handed a diff and a reason over a description of a change.",
        kind=MemoryKind.INFERENCE,
        method=ExtractionMethod.DERIVED_SUMMARY,
        origin=OriginType.AGENT,
        confidence=0.44,
        days_ago=22,
    )

    # --- Entities and the facts that mention them. The Atlas is built from this.
    await w.node(
        "e_northwind",
        type=ObjectType.ENTITY,
        content="Northwind Payments — Maya's employer. Series C, ~400 people, London and Austin.",
        days_ago=175,
    )
    await w.node(
        "e_ledger_svc",
        type=ObjectType.ENTITY,
        content="ledger-core — the Go service being written to replace the Rails ledger.",
        scope=ledger,
        days_ago=160,
    )
    await w.node(
        "e_priya",
        type=ObjectType.ENTITY,
        content="Devraj Mehta — staff engineer, the other person on the ledger rewrite.",
        days_ago=150,
    )
    await w.node(
        "e_tamsin",
        type=ObjectType.ENTITY,
        content="Tamsin Obi — Maya's manager, director of payments platform.",
        days_ago=150,
    )
    await w.node(
        "f_lag",
        type=ObjectType.FACT,
        content=(
            "The settlement worker's p99 lag is 8 seconds under normal load and "
            "spikes past 90 during the 02:00 batch."
        ),
        scope=oncall,
        confidence=0.88,
        origin=OriginType.AGENT,
        provider=Provider.CHATGPT,
        method=ExtractionMethod.MODEL_EXTRACTED,
        days_ago=68,
    )
    await w.node(
        "f_devraj",
        type=ObjectType.FACT,
        content="Devraj owns the reconciliation query and the backfill tooling.",
        scope=ledger,
        confidence=0.9,
        days_ago=100,
    )
    await w.node(
        "f_tamsin",
        type=ObjectType.FACT,
        content="Tamsin wants a written cutover plan two weeks before the date, every time.",
        confidence=0.9,
        days_ago=80,
    )
    await w.node(
        "f_stack",
        type=ObjectType.FACT,
        content="Northwind's stack is Go and Rails on AWS, Postgres, SQS, and Datadog.",
        confidence=0.93,
        days_ago=170,
    )

    await w.belongs("p_ledger", "dec_streams", "dec_outbox", "dec_no_k8s", "art_adr", "pref_go")
    await w.belongs("p_oncall", "art_runbook", "f_lag", "goal_pages")
    await w.belongs("p_hiring", "hiring_note")
    await w.link("dec_streams", "conv_events", EdgeType.DERIVED_FROM)
    await w.link("art_adr", "dec_streams", EdgeType.DERIVED_FROM)
    await w.mentions("e_northwind", "f_stack", "f_tamsin")
    await w.mentions("e_ledger_svc", "dec_streams", "dec_outbox", "pg_version", "f_lag")
    await w.mentions("e_priya", "f_devraj")
    await w.mentions("e_tamsin", "f_tamsin", "goal_promo")
    await w.link("inf_hours", "e_northwind", EdgeType.RELATES_TO, confidence=0.5)

    # --- The capture queue: turns the batch pass has not looked at yet. --------
    await w.episode(
        "ep_1",
        "ok so the reconciliation backfill is going to need to run against a read "
        "replica, the primary can't take another 200 iops of sequential scan while "
        "we're still dual-writing",
        surface=Provider.CLAUDE,
        scope=_project("proj_ledger_rewrite"),
    )
    await w.episode(
        "ep_2",
        "remind me — did we decide the outbox poller is one process with a lease or "
        "one per partition? I want to write it down before I forget again",
        surface=Provider.CHATGPT,
        scope=_project("proj_ledger_rewrite"),
    )
    await w.episode(
        "ep_3",
        "the 3am page last night was the settlement lag alarm again. third time this "
        "month. I think the alarm threshold is wrong, not the worker",
        surface=Provider.CLAUDE,
        scope=_project("proj_oncall"),
    )
    await w.episode(
        "ep_4",
        "I'm going to propose we cut the Rails ledger's write path entirely on "
        "cutover day rather than keeping it as a fallback. A fallback we never test "
        "is not a fallback.",
        surface=Provider.CLAUDE,
        pending=False,
        scope=_project("proj_ledger_rewrite"),
    )


# ---------------------------------------------------------------------------
# 2. The lawyer.
#
# The `Locality` case. Privilege is not a confidence level -- it is a statement that
# certain material may never reach certain surfaces, and a memory product that syncs
# everything to every assistant cannot express it at all. Every privileged object
# below is `local_only`, and the demo's point is that the workspace still holds and
# retrieves it while the ChatGPT compiler cannot see it.
# ---------------------------------------------------------------------------


async def _build_lawyer(w: Workspace) -> None:
    harbor = _project("matter_harborline")
    velasco = _project("matter_velasco")
    practice = _project("proj_practice")

    await w.node(
        "p_harbor",
        type=ObjectType.PROJECT,
        content=(
            "Matter 24-0881 — Harborline Logistics v. Castellan Freight. Breach of a "
            "five-year haulage agreement; we act for Harborline, the plaintiff. "
            "Filed in the Southern District of New York."
        ),
        scope=harbor,
        sensitivity=Sensitivity.SENSITIVE,
        days_ago=210,
    )
    await w.node(
        "p_velasco",
        type=ObjectType.PROJECT,
        content=(
            "Matter 24-1043 — Velasco Foods carve-out. Sell-side, advising on the "
            "disposal of the frozen division to a mid-market sponsor."
        ),
        scope=velasco,
        sensitivity=Sensitivity.SENSITIVE,
        days_ago=120,
    )
    await w.node(
        "p_practice",
        type=ObjectType.PROJECT,
        content="Practice management — templates, precedent bank, and how I want drafting done.",
        scope=practice,
        days_ago=300,
    )

    # --- Drafting preferences. Not privileged, and the most reused thing here. --
    await w.memory(
        "pref_plain",
        "Draft in plain English. No 'heretofore', no 'said agreement', no 'wherein'. "
        "If a sentence needs a second read to parse, rewrite it.",
        kind=MemoryKind.PREFERENCE,
        days_ago=290,
        confidence=0.97,
    )
    await w.memory(
        "pref_defined",
        "Defined terms in bold on first use, then never capitalised again unless "
        "they are defined. A capitalised term that isn't defined is a drafting bug.",
        kind=MemoryKind.PREFERENCE,
        scope=practice,
        days_ago=280,
    )
    await w.memory(
        "pref_cites",
        "Never give me a case citation you have not verified. If you are not certain "
        "a case exists, say so instead of producing a plausible cite.",
        kind=MemoryKind.INSTRUCTION,
        days_ago=275,
        confidence=0.98,
        method=ExtractionMethod.EXPLICIT_STATEMENT,
    )
    await w.memory(
        "pref_memo",
        "Memos open with the answer. Question, answer, then the reasoning — never "
        "the reasoning first.",
        kind=MemoryKind.PREFERENCE,
        method=ExtractionMethod.PROVIDER_CURATED,
        provider=Provider.CLAUDE,
        days_ago=260,
    )
    await w.memory(
        "pref_redline",
        "When you compare two drafts, give me a table of substantive changes only. "
        "I do not need to be told a comma moved.",
        kind=MemoryKind.INSTRUCTION,
        method=ExtractionMethod.MCP_LIVE_WRITE,
        provider=Provider.CLAUDE,
        days_ago=150,
    )

    # --- Privileged work product. Local model only, by construction. -----------
    await w.node(
        "dec_strategy",
        type=ObjectType.DECISION,
        content=(
            "Harborline: lead on the liquidated damages clause rather than lost "
            "profits. The clause is enforceable on its face and survives a "
            "penalty challenge; lost profits would put Harborline's own margin "
            "history into discovery."
        ),
        scope=harbor,
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        days_ago=160,
        confidence=0.95,
    )
    await w.memory(
        "priv_weakness",
        "Harborline's exposure: the 2023 service-level waivers were signed by a "
        "regional manager whose authority Castellan will dispute. Get ahead of it "
        "in the deposition prep.",
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        scope=harbor,
        days_ago=140,
        confidence=0.94,
    )
    await w.memory(
        "priv_settle",
        "Client's instruction: authority to settle up to $4.2m, and they want it "
        "resolved before the fiscal year closes in March.",
        kind=MemoryKind.INSTRUCTION,
        sensitivity=Sensitivity.RESTRICTED,
        locality=_only(Provider.LOCAL),
        scope=harbor,
        days_ago=90,
        confidence=0.96,
    )
    await w.memory(
        "priv_velasco",
        "Velasco: the frozen division's two largest supply contracts have change-of-"
        "control consents that nobody has asked for yet. This is the deal risk.",
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        scope=velasco,
        days_ago=70,
        confidence=0.93,
    )
    await w.node(
        "art_memo",
        type=ObjectType.ARTIFACT,
        content=(
            "Harborline — privileged memorandum on enforceability of clause 11.4 "
            "(liquidated damages). Draft 3, circulated internally only."
        ),
        scope=harbor,
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        origin=OriginType.AGENT,
        provider=Provider.LOCAL,
        days_ago=155,
    )

    # --- Not privileged: the procedural facts, which any assistant may hold. ----
    await w.node(
        "f_court",
        type=ObjectType.FACT,
        content=(
            "Harborline is before Judge Ellen Prasad, SDNY. She holds parties to "
            "her scheduling order and does not grant extensions on consent."
        ),
        scope=harbor,
        confidence=0.9,
        days_ago=180,
    )
    await w.node(
        "f_opposing",
        type=ObjectType.FACT,
        content=(
            "Opposing counsel on Harborline is Whitcombe & Roe; the partner is "
            "Gerald Roe, who litigates by motion volume rather than on the merits."
        ),
        scope=harbor,
        confidence=0.85,
        method=ExtractionMethod.MODEL_EXTRACTED,
        origin=OriginType.AGENT,
        provider=Provider.CLAUDE,
        days_ago=170,
    )
    await w.node(
        "conv_discovery",
        type=ObjectType.CONVERSATION,
        content=(
            "Working session on the scope of the document request — which custodians, "
            "which date range, and what to negotiate down."
        ),
        scope=harbor,
        origin=OriginType.AGENT,
        provider=Provider.CLAUDE,
        method=ExtractionMethod.BROWSER_CAPTURE,
        days_ago=120,
    )
    await w.node(
        "dec_custodians",
        type=ObjectType.DECISION,
        content=(
            "Agree to six custodians and a 30-month window, and push back on the "
            "request for Harborline's board minutes. The minutes are a fight worth "
            "having; the custodian count is not."
        ),
        scope=harbor,
        days_ago=118,
        confidence=0.92,
    )

    # --- A deadline that moved. The supersedes chain. --------------------------
    d1 = await w.memory(
        "depo_v1",
        "Castellan's 30(b)(6) deposition is set for 12 February.",
        scope=harbor,
        days_ago=60,
        confidence=0.9,
    )
    await w.memory(
        "depo_v2",
        "The 30(b)(6) moved to 6 March — Roe's third adjournment request, granted "
        "over our objection. Expert disclosure date did not move.",
        kind=MemoryKind.CORRECTION,
        supersedes=d1.id,
        scope=harbor,
        days_ago=12,
        confidence=0.95,
    )

    # --- A conflict worth surfacing. ------------------------------------------
    await w.memory(
        "gov_law_a",
        "The haulage agreement is governed by New York law.",
        scope=harbor,
        confidence=0.78,
        method=ExtractionMethod.MODEL_EXTRACTED,
        origin=OriginType.AGENT,
        provider=Provider.CHATGPT,
        days_ago=175,
    )
    await w.memory(
        "gov_law_b",
        "Clause 22 of the haulage agreement selects Delaware law, not New York — the "
        "2022 amendment changed it and the original schedule was never updated.",
        scope=harbor,
        confidence=0.81,
        method=ExtractionMethod.BROWSER_CAPTURE,
        origin=OriginType.AGENT,
        provider=Provider.CLAUDE,
        days_ago=58,
    )
    await w.link("gov_law_b", "gov_law_a", EdgeType.CONTRADICTS, confidence=0.9)

    # --- Time-bounded: an engagement that has an end. -------------------------
    await w.memory(
        "engagement",
        "Harborline's outside counsel engagement runs to 30 June and is billed at a "
        "blended rate, not standard rates.",
        scope=harbor,
        valid_from=w.days_ago(210),
        valid_until=w.days_ago(-160),
        days_ago=209,
        confidence=0.94,
    )

    await w.memory(
        "goal_harbor",
        "Resolve Harborline by mediation before the March fiscal close, or be ready "
        "for trial in June.",
        kind=MemoryKind.GOAL,
        scope=harbor,
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        days_ago=88,
    )
    await w.memory(
        "goal_precedent",
        "Build a real precedent bank this year so first-year associates stop "
        "redrafting the same indemnity from scratch.",
        kind=MemoryKind.GOAL,
        scope=practice,
        days_ago=200,
    )
    await w.memory(
        "inf_workload",
        "Carrying two active matters plus practice management; drafting happens "
        "early and calls cluster mid-afternoon.",
        kind=MemoryKind.INFERENCE,
        method=ExtractionMethod.DERIVED_SUMMARY,
        origin=OriginType.AGENT,
        confidence=0.46,
        days_ago=30,
    )

    await w.node(
        "e_harborline",
        type=ObjectType.ENTITY,
        content=(
            "Harborline Logistics — client, plaintiff in matter 24-0881. "
            "Regional freight forwarder."
        ),
        days_ago=209,
    )
    await w.node(
        "e_castellan",
        type=ObjectType.ENTITY,
        content="Castellan Freight — defendant in matter 24-0881.",
        days_ago=209,
    )
    await w.node(
        "e_roe",
        type=ObjectType.ENTITY,
        content="Gerald Roe — partner at Whitcombe & Roe, opposing counsel on Harborline.",
        days_ago=170,
    )
    await w.node(
        "e_velasco",
        type=ObjectType.ENTITY,
        content="Velasco Foods — client on the carve-out, matter 24-1043.",
        days_ago=120,
    )
    await w.node(
        "e_prasad",
        type=ObjectType.ENTITY,
        content="Judge Ellen Prasad — SDNY, presiding on matter 24-0881.",
        days_ago=180,
    )

    await w.belongs("p_harbor", "dec_strategy", "dec_custodians", "f_court", "art_memo", "depo_v2")
    await w.belongs("p_velasco", "priv_velasco")
    await w.belongs("p_practice", "pref_defined", "goal_precedent")
    await w.link("dec_custodians", "conv_discovery", EdgeType.DERIVED_FROM)
    await w.link("art_memo", "dec_strategy", EdgeType.DERIVED_FROM)
    await w.mentions("e_harborline", "priv_weakness", "priv_settle", "engagement", "goal_harbor")
    await w.mentions("e_castellan", "dec_strategy", "depo_v2")
    await w.mentions("e_roe", "f_opposing", "depo_v2")
    await w.mentions("e_velasco", "priv_velasco")
    await w.mentions("e_prasad", "f_court")

    await w.episode(
        "ep_1",
        "need to check whether Prasad has ever granted a fourth adjournment. if not "
        "we oppose and put the pattern in front of her",
        surface=Provider.CLAUDE,
        scope=harbor,
    )
    await w.episode(
        "ep_2",
        "draft the mediation statement opening — lead with the liquidated damages "
        "clause and the fact Castellan stopped paying while still taking deliveries",
        surface=Provider.CLAUDE,
        scope=harbor,
    )
    await w.episode(
        "ep_3",
        "for velasco: what consents are typically required on change of control in "
        "frozen food supply agreements? I want the general shape before I read ours",
        surface=Provider.CHATGPT,
        scope=velasco,
        pending=False,
    )


# ---------------------------------------------------------------------------
# 3. The investment banker.
#
# Locality and *time* together. Material non-public information is restricted while
# a deal is live and simply becomes public on announcement, which is the one case
# where `valid_from`/`valid_until` are not a nicety: an object whose restriction has
# expired is still the same object, and the audit has to be able to say what the
# rule was on a given day.
# ---------------------------------------------------------------------------


async def _build_banker(w: Workspace) -> None:
    meridian = _project("deal_meridian")
    lattice = _project("deal_lattice")
    coverage = _project("proj_coverage")

    await w.node(
        "p_meridian",
        type=ObjectType.PROJECT,
        content=(
            "Project Meridian — sell-side on a $1.4bn take-private of a listed "
            "vertical SaaS business. We advise the target's special committee. Live, "
            "unannounced."
        ),
        scope=meridian,
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        days_ago=85,
    )
    await w.node(
        "p_lattice",
        type=ObjectType.PROJECT,
        content=(
            "Project Lattice — buy-side advisory on a bolt-on acquisition in "
            "payments infrastructure. Announced 11 September; now public."
        ),
        scope=lattice,
        days_ago=190,
    )
    await w.node(
        "p_coverage",
        type=ObjectType.PROJECT,
        content=(
            "TMT coverage — the running view of the software and payments names I "
            "cover, and what each management team cares about."
        ),
        scope=coverage,
        days_ago=400,
    )

    # --- MNPI. Restricted and local-only, and the reason this persona exists. ---
    await w.memory(
        "mnpi_price",
        "Meridian: the committee will not recommend below $47.00 a share. The "
        "sponsor's last indication was $44.50 and they have room to $48.",
        sensitivity=Sensitivity.RESTRICTED,
        locality=_only(Provider.LOCAL),
        scope=meridian,
        days_ago=30,
        confidence=0.96,
        valid_from=w.days_ago(30),
    )
    await w.memory(
        "mnpi_timeline",
        "Meridian signing targeted for the second week of December, announcement the "
        "following Monday before market open.",
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        scope=meridian,
        days_ago=24,
        confidence=0.9,
    )
    await w.memory(
        "mnpi_diligence",
        "Meridian diligence issue: 19% of ARR sits with one customer whose contract "
        "renews four months after close. This is the value bridge argument.",
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        scope=meridian,
        days_ago=42,
        confidence=0.94,
    )
    await w.node(
        "dec_process",
        type=ObjectType.DECISION,
        content=(
            "Meridian: run a targeted process to five sponsors rather than a broad "
            "auction. A leak on a listed target moves the stock and costs the "
            "committee its negotiating position, and the strategic buyers in this "
            "vertical are all antitrust problems."
        ),
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        scope=meridian,
        days_ago=80,
        confidence=0.95,
    )
    await w.node(
        "art_model",
        type=ObjectType.ARTIFACT,
        content=(
            "Meridian — LBO model v14. Sponsor returns at $47.00 with 5.5x leverage "
            "and a 2029 exit at entry multiple."
        ),
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        scope=meridian,
        origin=OriginType.AGENT,
        provider=Provider.LOCAL,
        days_ago=28,
    )

    # --- Was restricted, is now public. The valid-time story. ------------------
    await w.memory(
        "lattice_public",
        "Lattice was announced on 11 September at $340m, 4.1x forward revenue. The "
        "deal thesis and the multiple are now public and usable in pitch material.",
        scope=lattice,
        days_ago=15,
        confidence=0.95,
        valid_from=w.days_ago(15),
    )
    await w.memory(
        "lattice_restricted",
        "Lattice was on the restricted list from 3 June until announcement on 11 "
        "September. No research, no trading, no pitch references in that window.",
        scope=lattice,
        sensitivity=Sensitivity.SENSITIVE,
        days_ago=150,
        confidence=0.95,
        valid_from=w.days_ago(150),
        valid_until=w.days_ago(15),
    )

    # --- The craft preferences, which are the reusable half of this workspace. --
    await w.memory(
        "pref_pages",
        "Every pitch page answers one question and says so in the header. If the "
        "header is a noun phrase, the page has not decided what it is arguing.",
        kind=MemoryKind.PREFERENCE,
        days_ago=380,
        confidence=0.96,
    )
    await w.memory(
        "pref_numbers",
        "Never hand me a number without its source and date. A multiple with no "
        "as-of date is not a fact.",
        kind=MemoryKind.INSTRUCTION,
        days_ago=370,
        confidence=0.97,
    )
    await w.memory(
        "pref_comps",
        "Comps are median and quartiles, never mean. One outlier at 14x makes a mean "
        "useless and someone always quotes it.",
        kind=MemoryKind.PREFERENCE,
        scope=coverage,
        method=ExtractionMethod.PROVIDER_CURATED,
        provider=Provider.CHATGPT,
        days_ago=300,
    )
    await w.memory(
        "pref_email",
        "Draft client emails at four sentences. If it needs more, it needs a call.",
        kind=MemoryKind.PREFERENCE,
        method=ExtractionMethod.MCP_LIVE_WRITE,
        provider=Provider.CLAUDE,
        days_ago=95,
    )
    await w.memory(
        "pref_no_mnpi",
        "Never put a live deal's name, price or timing into a hosted assistant. "
        "Codenames only, and only when the codename itself is not identifying.",
        kind=MemoryKind.INSTRUCTION,
        days_ago=360,
        confidence=0.99,
    )

    # --- A number that was revised. Supersedes chain. -------------------------
    m1 = await w.memory(
        "arr_v1",
        "Meridian target's FY25 ARR guide is $186m.",
        scope=meridian,
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        days_ago=70,
        confidence=0.88,
    )
    await w.memory(
        "arr_v2",
        "Meridian FY25 ARR revised to $179m after the churn true-up. The $186m "
        "figure was pre-adjustment and is in three pages of the deck.",
        kind=MemoryKind.CORRECTION,
        supersedes=m1.id,
        scope=meridian,
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        days_ago=20,
        confidence=0.94,
    )

    # --- Conflict. -----------------------------------------------------------
    await w.memory(
        "comp_a",
        "Sector trades at roughly 6.2x forward revenue for double-digit growers.",
        scope=coverage,
        confidence=0.7,
        method=ExtractionMethod.MODEL_EXTRACTED,
        origin=OriginType.AGENT,
        provider=Provider.CHATGPT,
        days_ago=120,
    )
    await w.memory(
        "comp_b",
        "Sector multiple has compressed to about 4.8x forward revenue since the "
        "summer rate move; the 6x prints are all pre-June.",
        scope=coverage,
        confidence=0.8,
        method=ExtractionMethod.EXPLICIT_STATEMENT,
        days_ago=25,
    )
    await w.link("comp_b", "comp_a", EdgeType.CONTRADICTS, confidence=0.88)

    await w.node(
        "conv_committee",
        type=ObjectType.CONVERSATION,
        content=(
            "Prep for the special committee call: how to frame the gap between the "
            "sponsor's indication and the committee's floor."
        ),
        scope=meridian,
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        origin=OriginType.AGENT,
        provider=Provider.LOCAL,
        days_ago=26,
    )
    await w.node(
        "f_coverage",
        type=ObjectType.FACT,
        content=(
            "Covers 24 names across vertical software and payments; eight are "
            "active dialogue, the rest are relationship maintenance."
        ),
        scope=coverage,
        confidence=0.9,
        days_ago=200,
    )
    await w.node(
        "f_compliance",
        type=ObjectType.FACT,
        content=(
            "Compliance requires a wall-crossing log entry before any name is "
            "discussed outside the deal team, including internally."
        ),
        confidence=0.95,
        days_ago=350,
    )

    await w.memory(
        "goal_meridian",
        "Get Meridian signed in December at or above $47.00.",
        kind=MemoryKind.GOAL,
        scope=meridian,
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        days_ago=79,
    )
    await w.memory(
        "goal_md",
        "Build the case for MD: two lead-left mandates and a repeatable sponsor "
        "dialogue in vertical software.",
        kind=MemoryKind.GOAL,
        sensitivity=Sensitivity.PERSONAL,
        locality=_only(Provider.LOCAL),
        days_ago=140,
    )
    await w.memory(
        "inf_travel",
        "Travels Tuesday to Thursday most weeks; drafting gets done on Sunday evening.",
        kind=MemoryKind.INFERENCE,
        method=ExtractionMethod.DERIVED_SUMMARY,
        origin=OriginType.AGENT,
        confidence=0.42,
        days_ago=40,
    )

    await w.node(
        "e_meridian",
        type=ObjectType.ENTITY,
        content="Project Meridian — codename for the live take-private. Restricted.",
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        days_ago=85,
    )
    await w.node(
        "e_lattice",
        type=ObjectType.ENTITY,
        content="Project Lattice — announced payments bolt-on, now public.",
        days_ago=190,
    )
    await w.node(
        "e_committee",
        type=ObjectType.ENTITY,
        content=(
            "Meridian special committee — three independent directors, chaired by "
            "the audit chair."
        ),
        sensitivity=Sensitivity.SENSITIVE,
        locality=_only(Provider.LOCAL),
        days_ago=84,
    )
    await w.node(
        "e_compliance",
        type=ObjectType.ENTITY,
        content="Control room — the compliance function that keeps the restricted list.",
        days_ago=350,
    )

    await w.belongs("p_meridian", "dec_process", "art_model", "mnpi_price", "goal_meridian")
    await w.belongs("p_lattice", "lattice_public", "lattice_restricted")
    await w.belongs("p_coverage", "pref_comps", "f_coverage", "comp_b")
    await w.link("dec_process", "conv_committee", EdgeType.DERIVED_FROM)
    await w.link("art_model", "mnpi_price", EdgeType.DERIVED_FROM)
    await w.mentions("e_meridian", "mnpi_price", "mnpi_timeline", "mnpi_diligence", "arr_v2")
    await w.mentions("e_lattice", "lattice_public", "lattice_restricted")
    await w.mentions("e_committee", "dec_process", "conv_committee")
    await w.mentions("e_compliance", "f_compliance", "pref_no_mnpi")

    await w.episode(
        "ep_1",
        "need a page comparing our floor to the last three take-privates in vertical "
        "software by premium to unaffected — no names in the prompt, just the shape",
        surface=Provider.CHATGPT,
        scope=coverage,
    )
    await w.episode(
        "ep_2",
        "what's the standard market check language when a special committee runs a "
        "targeted process rather than a broad auction?",
        surface=Provider.CHATGPT,
        scope=coverage,
    )
    await w.episode(
        "ep_3",
        "the churn true-up moved ARR by 7m. I need to know every page of the deck "
        "that quotes the old number before this goes back to the committee",
        surface=Provider.LOCAL,
        scope=meridian,
    )
    await w.episode(
        "ep_4",
        "draft the four-sentence note to the committee chair confirming Thursday's call",
        surface=Provider.CLAUDE,
        pending=False,
        scope=meridian,
    )


# ---------------------------------------------------------------------------
# Traffic profiles.
#
# One per persona, and the topics are chosen to make the idea-over-time view
# answerable rather than decorative: in each profile something is winding down
# while something else is ramping up, so "is this topic growing" has a true
# answer that the chart can be checked against.
# ---------------------------------------------------------------------------

_ENGINEER_TRAFFIC = TrafficProfile(
    key="engineer",
    days=190,
    providers={Provider.CLAUDE: 5.0, Provider.CHATGPT: 3.5, Provider.LOCAL: 1.2},
    surfaces={"mcp": 7.0, "proxy": 2.0, "cli": 1.0},
    reads_per_day=8.0,
    questions=[
        "what did we decide about the ledger cutover",
        "how do we handle idempotency keys",
        "what is the on-call escalation path",
        "which Postgres version are we targeting",
        "what did I say about the hiring loop",
        "remind me what the rollback plan is",
        "what are my code review preferences",
        "who owns the settlement service",
    ],
    topics=[
        Topic(
            label="ledger rewrite",
            scope=_project("proj_ledger_rewrite"),
            kinds=[MemoryKind.FACT, MemoryKind.GOAL],
            weight_curve=(1.0, 0.25),
            sentences=[
                "The ledger rewrite replaces the double-entry tables, not the reporting views.",
                "Cutover is gated on the settlement service reaching 99.95% over a full week.",
                "We write to both ledgers during the dual-write window and read from the old one.",
                "Idempotency keys are the client's request id, not a server-generated UUID.",
                "The reconciliation job runs hourly during dual-write and nightly after.",
                "We are not migrating the 2019 archive; it stays queryable in the old schema.",
                "Rollback means flipping the read path back, not restoring a backup.",
                "The ledger team owns the settlement service after the cutover, not payments.",
                "Postgres 16 is the target; the 15 pin was a staging artefact.",
                "Money amounts are stored as integer minor units, never as numeric.",
                "Currency conversion happens at capture time and is recorded on the entry.",
                "The dual-write window is two weeks, extended only by an explicit decision.",
                "Partial failures during dual-write are retried, never silently dropped.",
                "We agreed not to add a new queue for the rewrite; existing Kafka topics only.",
                "The cutover runbook lives in the ledger repo, not the ops wiki.",
                "Schema changes during dual-write need sign-off from both teams.",
                "Reporting views are frozen for the duration of the cutover.",
                "The old ledger stays writable for 30 days after cutover as an escape hatch.",
            ],
        ),
        Topic(
            label="on-call and reliability",
            scope=_project("proj_oncall"),
            kinds=[MemoryKind.FACT, MemoryKind.INSTRUCTION, MemoryKind.PREFERENCE],
            weight_curve=(0.7, 0.9),
            sentences=[
                "On-call escalates to the secondary after 15 minutes without acknowledgement.",
                "Page only on customer-visible symptoms; queue depth alerts go to a channel.",
                "The settlement service has its own rotation, separate from platform.",
                "Post-incident reviews happen within two working days or they do not happen.",
                "We do not page for a single failed reconciliation run; two consecutive ones page.",
                "Runbook links go in the alert payload, not in a separate wiki page.",
                "Severity 1 means money is wrong or stuck; everything else is at most a 2.",
                "The on-call handover note is required even when the week was quiet.",
                "Dashboards live in Grafana; alert rules live in the service repo.",
                "Silences longer than 24 hours need a linked ticket.",
                "I prefer incident timelines written in UTC with local times in brackets.",
                "The escalation policy changed in April: platform lead is now the third step.",
                "We stopped paging on p99 latency alone after the March false-positive run.",
                "Every incident gets a one-line summary before anyone writes the long version.",
            ],
        ),
        Topic(
            label="hiring loop",
            scope=_project("proj_hiring"),
            kinds=[MemoryKind.PREFERENCE, MemoryKind.INSTRUCTION, MemoryKind.GOAL],
            weight_curve=(0.15, 1.0),
            sentences=[
                "The systems interview is 60 minutes and always has a written component.",
                "We stopped asking the distributed-locking question; it selected for trivia.",
                "Two strong yeses and no strong no is a hire; anything else goes to committee.",
                "Debrief notes are written before the debrief, not during it.",
                "Candidates get the take-home brief only after the first call, never before.",
                "I want the loop to include one person from outside the hiring team.",
                "We are hiring for the settlement service first, platform second.",
                "The bar for staff is independent judgement under ambiguity, not output volume.",
                "Interview feedback is due within 24 hours or the slot is not counted.",
                "We do not do whiteboard algorithm rounds any more.",
                "The take-home is capped at three hours and we say so in writing.",
                "Referrals skip the recruiter screen but not the technical screen.",
                "Panel composition is fixed before the first candidate enters the loop.",
                "I am the hiring manager for the two staff roles opened in August.",
            ],
        ),
        Topic(
            label="working preferences",
            scope=GLOBAL_SCOPE,
            kinds=[MemoryKind.PREFERENCE, MemoryKind.INSTRUCTION],
            weight_curve=(0.9, 0.6),
            sentences=[
                "I want code review comments to name the risk, not just the style issue.",
                "Prefer explicit types over inference in anything crossing a module boundary.",
                "Do not suggest a dependency without a reason that survives being said aloud.",
                "I read diffs before descriptions; lead with the change, not the context.",
                "Write commit messages in the imperative mood.",
                "I prefer one long function to five that are only called once each.",
                "Tests that need a comment to explain the assertion are testing the wrong thing.",
                "Use UTC everywhere in code; localise only at the display boundary.",
                "I would rather have a failing test than a skipped one.",
                "Ask before refactoring something I did not mention.",
            ],
        ),
        Topic(
            label="context portability",
            scope=GLOBAL_SCOPE,
            kinds=[MemoryKind.GOAL, MemoryKind.FACT],
            weight_curve=(0.1, 1.0),
            sentences=[
                "I want the same project context in Claude and ChatGPT without re-explaining it.",
                "Switching assistants mid-task currently costs me about ten minutes of setup.",
                "The decisions I lose are the ones made in whichever tool I had open.",
                "I keep a scratch file of context purely to paste into new chats.",
                "What I actually want is one memory that every tool reads from.",
                "Per-product memory features only remember what was said to that product.",
                "I would trade some recall for being able to see why a fact was kept.",
            ],
        ),
    ],
)

_LAWYER_TRAFFIC = TrafficProfile(
    key="lawyer",
    days=190,
    providers={Provider.CLAUDE: 4.0, Provider.LOCAL: 4.5, Provider.CHATGPT: 1.0},
    surfaces={"mcp": 6.0, "cli": 3.0, "proxy": 1.0},
    reads_per_day=6.0,
    query_text_share=0.18,
    questions=[
        "what is my preferred drafting style for indemnities",
        "what did we agree on governing law",
        "summarise the position on the limitation clause",
        "what are the filing deadlines on this matter",
        "how do I usually structure a witness statement",
    ],
    topics=[
        Topic(
            label="drafting style",
            scope=GLOBAL_SCOPE,
            kinds=[MemoryKind.PREFERENCE, MemoryKind.INSTRUCTION],
            weight_curve=(1.0, 0.8),
            sentences=[
                "Defined terms are capitalised and listed once, at the front.",
                "I do not use 'shall' in new drafts; obligations take 'must'.",
                "Indemnities are drafted as standalone clauses, never nested in warranties.",
                "Cross-references use clause numbers, never 'above' or 'below'.",
                "Every limitation of liability gets a carve-out list in the same clause.",
                "Numbered sub-paragraphs stop at three levels.",
                "I prefer a short recitals section that states the commercial purpose.",
                "Boilerplate goes last and is never the first thing negotiated.",
                "Time periods are stated in business days with the jurisdiction named.",
                "Draft notes to the client are written in plain English, not clause language.",
                "I want the counterparty's changes tracked and summarised before I read them.",
                "Never let an entire agreement clause sit above the dispute resolution clause.",
            ],
        ),
        Topic(
            label="Harrow matter",
            scope=_project("proj_harrow"),
            kinds=[MemoryKind.FACT, MemoryKind.GOAL],
            weight_curve=(1.0, 0.2),
            sensitivity=Sensitivity.SENSITIVE,
            locality=_only(Provider.LOCAL),
            sentences=[
                "The Harrow dispute turns on whether the variation was agreed orally.",
                "Our client's position is that the site meeting minutes are the best record.",
                "The limitation period expires in the second week of November.",
                "Counsel's preliminary view is that quantum is the weaker half of the claim.",
                "We are not pleading fraud; the evidence does not support it.",
                "Disclosure is scheduled before the costs budget is filed.",
                "The governing law clause points to England and Wales despite the Irish entity.",
                "Witness statements are limited to the variation question.",
                "Settlement authority runs to the figure discussed on the September call.",
                "The expert is instructed on causation only, not on quantum.",
            ],
        ),
        Topic(
            label="Meridian matter",
            scope=_project("proj_meridian"),
            kinds=[MemoryKind.FACT, MemoryKind.GOAL],
            weight_curve=(0.1, 1.0),
            sensitivity=Sensitivity.SENSITIVE,
            locality=_only(Provider.LOCAL),
            sentences=[
                "Meridian is a contractual dispute over a services agreement termination.",
                "The termination notice was served two days outside the contractual window.",
                "Our argument is that the cure period was never validly triggered.",
                "The client wants a commercial outcome, not a judgment.",
                "Mediation is listed for the first week of the new quarter.",
                "The counterparty has changed solicitors twice since the claim was issued.",
                "We hold the original signed agreement; they rely on an unsigned version.",
                "Costs to date are approaching the budgeted figure for the whole phase.",
                "The committee chair wants a four-sentence update before each call.",
            ],
        ),
        Topic(
            label="practice management",
            scope=GLOBAL_SCOPE,
            kinds=[MemoryKind.PREFERENCE, MemoryKind.INSTRUCTION, MemoryKind.GOAL],
            weight_curve=(0.5, 1.0),
            sentences=[
                "Time is recorded the same day; nothing is reconstructed at month end.",
                "Client updates go out on Thursdays unless something has moved.",
                "I review the matter list every Monday morning before anything else.",
                "Privileged material never goes to a hosted assistant.",
                "Draft correspondence is always reviewed by a second pair of eyes.",
                "I want deadlines in the calendar with a two-week warning, not a two-day one.",
                "Supervision notes for the junior are written monthly.",
            ],
        ),
    ],
)

_BANKER_TRAFFIC = TrafficProfile(
    key="banker",
    days=190,
    providers={Provider.CHATGPT: 4.0, Provider.CLAUDE: 3.0, Provider.LOCAL: 3.0},
    surfaces={"mcp": 6.5, "proxy": 2.5, "cli": 1.0},
    reads_per_day=6.5,
    query_text_share=0.15,
    topics=[
        Topic(
            label="sector view",
            scope=GLOBAL_SCOPE,
            kinds=[MemoryKind.FACT, MemoryKind.INFERENCE],
            weight_curve=(0.8, 1.0),
            sentences=[
                "Software multiples in the mid-market have compressed about two turns this year.",
                "Strategic buyers are moving faster than sponsors in this cycle.",
                "Carve-outs are taking longer to sign than platform deals.",
                "Data-centre adjacency is the theme every board wants covered.",
                "The IPO window reopened for profitable growth, not for growth alone.",
                "Debt markets will support five turns for a recurring-revenue business.",
                "Management presentations are running shorter than they did last year.",
                "Cross-border approvals are the schedule risk nobody prices correctly.",
                "Sponsors are asking for longer exclusivity than they did in the spring.",
            ],
        ),
        Topic(
            label="Kestrel deal",
            scope=_project("proj_kestrel"),
            kinds=[MemoryKind.FACT, MemoryKind.GOAL],
            weight_curve=(1.0, 0.15),
            sensitivity=Sensitivity.RESTRICTED,
            locality=_only(Provider.LOCAL),
            sentences=[
                "Kestrel is a take-private of a listed software business.",
                "The consortium is two sponsors plus a sovereign co-investor.",
                "Diligence flagged a customer concentration issue in the top five accounts.",
                "The board wants a premium in the low thirties to recommend.",
                "Announcement is targeted for the week after the quarterly results.",
                "Financing is committed subject to the usual certain-funds conditions.",
                "The management team is rolling a meaningful share of their equity.",
            ],
        ),
        Topic(
            label="Tessera deal",
            scope=_project("proj_tessera"),
            kinds=[MemoryKind.FACT, MemoryKind.GOAL],
            weight_curve=(0.1, 1.0),
            sentences=[
                "Tessera announced on the fourth, so the restriction lapsed on announcement.",
                "The buyer is a strategic acquirer in the same vertical.",
                "Synergy case rests on consolidating two overlapping sales organisations.",
                "Regulatory review is expected to be a phase-one clearance.",
                "The earn-out is two years and tied to net revenue retention.",
                "Integration planning started before signing, which is unusual for them.",
            ],
        ),
        Topic(
            label="working preferences",
            scope=GLOBAL_SCOPE,
            kinds=[MemoryKind.PREFERENCE, MemoryKind.INSTRUCTION],
            weight_curve=(0.9, 0.7),
            sentences=[
                "Every model output gets a sources line or it does not go in the deck.",
                "I want the comps table sorted by EV/EBITDA, not alphabetically.",
                "Never put a live deal name into a hosted assistant.",
                "Client-ready pages are checked by a second person before they leave.",
                "I prefer one page of judgement to ten pages of output.",
                "Numbers in a deck are rounded consistently or not at all.",
                "Deal code names are used in every internal document, including drafts.",
            ],
        ),
    ],
    questions=[
        "what is the status of the Tessera integration case",
        "what are current software multiples",
        "what did I say about deck formatting",
        "which deals are restricted right now",
    ],
)

TRAFFIC_BY_KEY: dict[str, TrafficProfile] = {
    "engineer": _ENGINEER_TRAFFIC,
    "lawyer": _LAWYER_TRAFFIC,
    "banker": _BANKER_TRAFFIC,
}


PERSONAS: tuple[Persona, ...] = (
    Persona(
        key="engineer",
        name="Maya Okonkwo",
        email="maya.okonkwo@demo.coleta.app",
        title="Staff Software Engineer, Northwind Payments",
        headline=(
            "The portability case: decisions reached in one assistant, needed in "
            "another. Two live projects, a cutover date that moved twice, and a "
            "Postgres version two assistants disagree about."
        ),
        build=_build_engineer,
    ),
    Persona(
        key="lawyer",
        name="Adaeze Whitford",
        email="adaeze.whitford@demo.coleta.app",
        title="Senior Associate, commercial litigation",
        headline=(
            "The privilege case: drafting preferences travel to every assistant, "
            "privileged work product reaches only the local model. Two matters, and "
            "a governing-law clause the two assistants read differently."
        ),
        build=_build_lawyer,
    ),
    Persona(
        key="banker",
        name="Rohan Deshpande",
        email="rohan.deshpande@demo.coleta.app",
        title="Vice President, TMT coverage",
        headline=(
            "The MNPI case: a live deal walled off from every hosted assistant, and "
            "a second deal whose restriction expired on announcement — which is "
            "valid-time doing real work rather than demonstrating a field."
        ),
        build=_build_banker,
    ),
)

PERSONAS_BY_KEY = {p.key: p for p in PERSONAS}


async def build_persona(
    store: Store,
    tenant_id: TenantId,
    persona: Persona,
    *,
    now: datetime | None = None,
    with_history: bool = True,
) -> DemoResult:
    """Build one persona's graph into `tenant_id`.

    Not idempotent: it mints fresh ids on every call, exactly as `seed.seed` does.
    Running it twice into one tenant gives you two of everything, so the caller is
    responsible for deciding a tenant is empty first.

    `with_history` adds the backfill and the traffic: a few hundred writes spread
    across six months and the reads that went with them. It is separable because
    the tests that assert on a persona's hand-written facts should not have to
    count past a generated corpus, and because it is the slow half.
    """
    workspace = Workspace(store, tenant_id, now=now or _now())
    await persona.build(workspace)

    profile = TRAFFIC_BY_KEY.get(persona.key)
    if with_history and profile is not None:
        # Curated facts first, generated volume second, reads over both: the
        # traffic pass has to be able to return the hand-written objects too, or
        # the interesting facts are the only ones nothing ever read.
        curated = [role for role in workspace.result.objects if not role.startswith("ep_")]
        # Order is the story: the import lands first, the user writes over it for
        # six months, and the reads run across the whole span.
        imported = await import_burst(workspace, profile)
        generated = await backfill_topics(workspace, profile)
        await simulate_traffic(workspace, profile, curated + imported + generated)

    return workspace.result
