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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from coletar.capture import capture_turn
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

    async def _put(self, role: str, obj: ContextObject) -> ContextObject:
        stored = await self._store.put_object(self._tenant, obj)
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
    store: Store, tenant_id: TenantId, persona: Persona, *, now: datetime | None = None
) -> DemoResult:
    """Build one persona's graph into `tenant_id`.

    Not idempotent: it mints fresh ids on every call, exactly as `seed.seed` does.
    Running it twice into one tenant gives you two of everything, so the caller is
    responsible for deciding a tenant is empty first.
    """
    workspace = Workspace(store, tenant_id, now=now or _now())
    await persona.build(workspace)
    return workspace.result
