"""Wire schemas for the MCP tools (SCOPE §2, §9).

These exist so tool responses are *typed*, not hand-assembled dicts. The build
plan's acceptance criterion is that `search_context` responses conform to the M1.1
Memory schema; a Pydantic model makes that a property the type checker and the MCP
schema generator both enforce, rather than something a hand-built dict can drift
away from silently.

Provenance rides on every object that crosses the wire. A caller that cannot see
where a memory came from cannot decide how much to trust it, and AGENTS.md makes
provenance non-optional end to end -- including at the edge.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from coletar.schema.objects import (
    ContextObject,
    ExtractionMethod,
    MemoryKind,
    ObjectType,
    OriginType,
    Provider,
    Sensitivity,
)


class ProvenanceView(BaseModel):
    """§2 provenance, minus internals a caller has no use for."""

    origin_type: OriginType
    provider: Provider
    confidence: float
    captured_at: datetime
    source_object_ids: list[str] = Field(default_factory=list)


class ObjectView(BaseModel):
    """One canonical object as a connected model sees it."""

    id: str
    type: ObjectType
    content: str
    scope: str
    confidence: float
    extraction_method: ExtractionMethod
    sensitivity: Sensitivity
    supersedes: str | None = None
    provenance: ProvenanceView
    updated_at: datetime
    #: Present only on search results.
    kind: MemoryKind | None = None
    score: float | None = None

    @classmethod
    def of(cls, obj: ContextObject, score: float | None = None) -> ObjectView:
        return cls(
            id=obj.id,
            type=obj.type,
            content=obj.content,
            scope=str(obj.scope),
            confidence=round(obj.confidence, 3),
            extraction_method=obj.extraction_method,
            sensitivity=obj.sensitivity,
            supersedes=obj.supersedes,
            provenance=ProvenanceView(
                origin_type=obj.provenance.origin_type,
                provider=obj.provenance.provider,
                confidence=round(obj.provenance.confidence, 3),
                captured_at=obj.provenance.captured_at,
                source_object_ids=list(obj.provenance.source_object_ids),
            ),
            updated_at=obj.updated_at,
            kind=getattr(obj, "kind", None),
            score=None if score is None else round(score, 4),
        )


class ScoreExplanation(BaseModel):
    """The arithmetic behind one hit. Present only under `explain=True`, and carried
    from the ranking path rather than recomputed, so it cannot disagree with the
    score it explains."""

    vector: float
    lexical: float
    confidence_factor: float
    recency_factor: float
    relevance: float
    total: float
    source: str


class SearchContextResponse(BaseModel):
    results: list[ObjectView]
    token_estimate: int
    truncated: bool
    #: Omitted entirely unless the caller asked to explain, so `explain` adds a
    #: field rather than changing the default response shape.
    explanations: list[ScoreExplanation] | None = None


class WriteMemoryResponse(BaseModel):
    id: str
    stored: bool
    confidence: float
    scope: str
    kind: MemoryKind


class ProjectStateResponse(BaseModel):
    project_id: str
    count: int
    objects: dict[str, list[ObjectView]]


class OpenLoopsResponse(BaseModel):
    count: int
    open_loops: list[ObjectView]
