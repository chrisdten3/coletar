"""Seed corpus: one object of every type, plus a supersedes chain.

M1.1's fixture deliverable, and it lives in the package rather than in `tests/`
because later milestones need it too -- the compression job (§6), the Context
Inspector (§8.2) and every compiler want a graph that exercises each object type,
each scope, and at least one correction chain, without each of them inventing its
own.

The persona is deliberately concrete. A corpus of "fact 1", "fact 2" will pass a
retrieval test and tell you nothing about whether retrieval works.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from coletar.schema.objects import (
    GLOBAL_SCOPE,
    ContextObject,
    Edge,
    EdgeType,
    ExtractionMethod,
    Memory,
    MemoryKind,
    ObjectType,
    OriginType,
    Provenance,
    Provider,
    Scope,
    ScopeType,
)
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

PROJECT_ID = "proj_ledger"
PROJECT_SCOPE = Scope(type=ScopeType.PROJECT, id=PROJECT_ID)
OTHER_PROJECT_SCOPE = Scope(type=ScopeType.PROJECT, id="proj_unrelated")


@dataclass
class SeedResult:
    """Ids by role, so a test can assert about a specific object without
    re-deriving which one it was."""

    by_role: dict[str, str] = field(default_factory=dict)
    supersedes_chain: list[str] = field(default_factory=list)

    def id(self, role: str) -> str:
        return self.by_role[role]


def _object(
    *,
    type: ObjectType,
    content: str,
    scope: Scope = GLOBAL_SCOPE,
    extraction_method: ExtractionMethod = ExtractionMethod.EXPLICIT_STATEMENT,
    origin_type: OriginType = OriginType.USER,
    provider: Provider = Provider.COLETAR,
    confidence: float = 0.9,
) -> ContextObject:
    return ContextObject(
        type=type,
        content=content,
        scope=scope,
        confidence=confidence,
        extraction_method=extraction_method,
        provenance=Provenance(
            origin_type=origin_type, provider=provider, confidence=confidence
        ),
    )


async def seed(store: Store, tenant_id: TenantId) -> SeedResult:
    """Populate `store` with the canonical fixture graph. Idempotent per store
    instance only -- it mints fresh ids on every call."""
    result = SeedResult()

    async def put(role: str, obj: ContextObject) -> ContextObject:
        stored = await store.put_object(tenant_id, obj)
        result.by_role[role] = stored.id
        return stored

    project = await put(
        "project",
        _object(
            type=ObjectType.PROJECT,
            content="Ledger — a double-entry bookkeeping service for freelancers.",
            scope=PROJECT_SCOPE,
        ),
    )
    conversation = await put(
        "conversation",
        _object(
            type=ObjectType.CONVERSATION,
            content="Design session on how Ledger should represent monetary amounts.",
            scope=PROJECT_SCOPE,
            origin_type=OriginType.AGENT,
            provider=Provider.CLAUDE,
        ),
    )
    decision = await put(
        "decision",
        _object(
            type=ObjectType.DECISION,
            content=(
                "Ledger stores money as integer minor units, never floating point. "
                "Rounding happens once, at presentation."
            ),
            scope=PROJECT_SCOPE,
        ),
    )
    artifact = await put(
        "artifact",
        _object(
            type=ObjectType.ARTIFACT,
            content="ledger/money.py — the Money value type and its arithmetic.",
            scope=PROJECT_SCOPE,
            origin_type=OriginType.AGENT,
        ),
    )

    # The agentic-graph triple (§6): three more object types on the same graph, not
    # a parallel store.
    await put(
        "entity",
        _object(type=ObjectType.ENTITY, content="Acme Corp — Chris's former employer."),
    )
    await put(
        "fact",
        _object(
            type=ObjectType.FACT,
            content="Ledger's test suite runs under pytest with asyncio_mode=auto.",
            scope=PROJECT_SCOPE,
            origin_type=OriginType.AGENT,
        ),
    )
    await put(
        "episode",
        _object(
            type=ObjectType.EPISODE,
            content="Migrated Ledger's currency handling off floats in March.",
            scope=PROJECT_SCOPE,
            origin_type=OriginType.AGENT,
        ),
    )

    # Global memories: the user's own preferences, which apply inside any project.
    await put(
        "preference",
        Memory.from_write(
            "I prefer fixed-point integers over doubles for money.",
            kind=MemoryKind.PREFERENCE,
            extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
            origin_type=OriginType.USER,
        ),
    )
    await put(
        "instruction",
        Memory.from_write(
            "Always show me the failing test output before proposing a fix.",
            kind=MemoryKind.INSTRUCTION,
            extraction_method=ExtractionMethod.MCP_LIVE_WRITE,
        ),
    )
    await put(
        "goal",
        Memory.from_write(
            "Ship Ledger's invoicing module before the end of the quarter.",
            kind=MemoryKind.GOAL,
            scope=PROJECT_SCOPE,
        ),
    )
    await put(
        "inference",
        Memory.from_write(
            "Chris works late in the evening rather than early in the morning.",
            kind=MemoryKind.INFERENCE,
            extraction_method=ExtractionMethod.DERIVED_SUMMARY,
            origin_type=OriginType.AGENT,
        ),
    )
    # Another project's object, so scope-isolation tests have something to fail on.
    await put(
        "other_project_memory",
        Memory.from_write(
            "The Atlas prototype is written in Rust.",
            scope=OTHER_PROJECT_SCOPE,
        ),
    )

    # A three-link supersedes chain: two corrections over one original fact.
    original = await put(
        "employer_v1",
        Memory.from_write("Chris works at Acme Corp.", kind=MemoryKind.FACT),
    )
    corrected = await put(
        "employer_v2",
        Memory.from_write(
            "Chris works at Globex.", kind=MemoryKind.CORRECTION, supersedes=original.id
        ),
    )
    current = await put(
        "employer_v3",
        Memory.from_write(
            "Chris is independent and consults through his own studio.",
            kind=MemoryKind.CORRECTION,
            supersedes=corrected.id,
        ),
    )
    result.supersedes_chain = [original.id, corrected.id, current.id]

    for src, dst, edge_type in (
        (corrected.id, original.id, EdgeType.SUPERSEDES),
        (current.id, corrected.id, EdgeType.SUPERSEDES),
        (decision.id, conversation.id, EdgeType.DERIVED_FROM),
        (artifact.id, project.id, EdgeType.BELONGS_TO),
        (conversation.id, project.id, EdgeType.BELONGS_TO),
        (decision.id, project.id, EdgeType.BELONGS_TO),
    ):
        await store.add_edge(tenant_id, Edge(src_id=src, dst_id=dst, type=edge_type))

    return result
