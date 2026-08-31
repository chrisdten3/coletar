"""Canonical object model (SCOPE §2).

The discipline this module enforces: **Memory is a subtype, not a special case.**
Every kind of context object -- Project, Conversation, Decision, Artifact, Memory --
is a `ContextObject` with the same identity, scope, provenance, versioning and edge
semantics. Anything that only makes sense for one subtype lives in `payload`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _now() -> datetime:
    return datetime.now(UTC)


class ObjectType(StrEnum):
    """Every row in the canonical graph is one of these."""

    PROJECT = "project"
    CONVERSATION = "conversation"
    DECISION = "decision"
    ARTIFACT = "artifact"
    MEMORY = "memory"
    # §6: the Zep/Graphiti-style agentic view is three more types, not a parallel model.
    ENTITY = "entity"
    FACT = "fact"
    EPISODE = "episode"


_ID_PREFIX: dict[ObjectType, str] = {
    ObjectType.PROJECT: "proj",
    ObjectType.CONVERSATION: "conv",
    ObjectType.DECISION: "dec",
    ObjectType.ARTIFACT: "art",
    ObjectType.MEMORY: "mem",
    ObjectType.ENTITY: "ent",
    ObjectType.FACT: "fact",
    ObjectType.EPISODE: "ep",
}


def new_id(object_type: ObjectType) -> str:
    """`mem_3f9a...` -- prefixed so an id is self-describing in logs and manifests."""
    return f"{_ID_PREFIX[object_type]}_{uuid.uuid4().hex[:16]}"


class MemoryKind(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    INSTRUCTION = "instruction"
    GOAL = "goal"
    CORRECTION = "correction"
    INFERENCE = "inference"


class ExtractionMethod(StrEnum):
    """How the object came to exist.

    This is the field v0.1 lacked. It is what lets the Context Inspector show a user
    *how sure* the system is and *where a memory came from*, and it is the input to
    `default_confidence` below -- a typed tool call from a live connector is a much
    stronger signal than a line recovered from a raw export.
    """

    EXPLICIT_STATEMENT = "explicit_statement"
    ACCOUNT_EXPORT_PARSE = "account_export_parse"
    BROWSER_CAPTURE = "browser_capture"
    MCP_LIVE_WRITE = "mcp_live_write"
    DERIVED_SUMMARY = "derived_summary"


#: §3.1: connector writes arrive already typed, so they outrank export parsing.
DEFAULT_CONFIDENCE: dict[ExtractionMethod, float] = {
    ExtractionMethod.EXPLICIT_STATEMENT: 0.95,
    ExtractionMethod.MCP_LIVE_WRITE: 0.90,
    ExtractionMethod.BROWSER_CAPTURE: 0.70,
    ExtractionMethod.ACCOUNT_EXPORT_PARSE: 0.60,
    ExtractionMethod.DERIVED_SUMMARY: 0.50,
}


def default_confidence(method: ExtractionMethod) -> float:
    return DEFAULT_CONFIDENCE[method]


class Sensitivity(StrEnum):
    NORMAL = "normal"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


class OriginType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class Provider(StrEnum):
    CLAUDE = "claude"
    CHATGPT = "chatgpt"
    GEMINI = "gemini"
    LOCAL = "local"
    COLETAR = "coletar"


class ScopeType(StrEnum):
    GLOBAL = "global"
    PROJECT = "project"


Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class Scope(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: ScopeType = ScopeType.GLOBAL
    id: str | None = None

    @model_validator(mode="after")
    def _project_scope_needs_id(self) -> Self:
        if self.type is ScopeType.PROJECT and not self.id:
            raise ValueError("project scope requires an id")
        if self.type is ScopeType.GLOBAL and self.id:
            raise ValueError("global scope must not carry an id")
        return self

    def __str__(self) -> str:
        return "global" if self.type is ScopeType.GLOBAL else f"project:{self.id}"


GLOBAL_SCOPE = Scope(type=ScopeType.GLOBAL)


class LocalityMode(StrEnum):
    SYNCED = "synced"
    LOCAL_ONLY = "local_only"


class Locality(BaseModel):
    """Which surfaces may read this object back, independent of `Scope`.

    `Scope` answers "which container is this in" (global vs. one project);
    this answers "which connected surfaces can this propagate to." Cross-surface
    propagation is the product's central claim (§10 step 2), so the default is
    `SYNCED` -- every write is visible to every surface the tenant has connected,
    unchanged from behavior before this field existed. `LOCAL_ONLY` is an opt-in
    per object, never a global switch: a user keeps one thing on one surface
    without turning off portability for everything else.
    """

    model_config = ConfigDict(frozen=True)

    mode: LocalityMode = LocalityMode.SYNCED
    surfaces: frozenset[Provider] = frozenset()

    @model_validator(mode="after")
    def _local_only_needs_surfaces(self) -> Self:
        if self.mode is LocalityMode.LOCAL_ONLY and not self.surfaces:
            raise ValueError("local_only locality requires at least one surface")
        if self.mode is LocalityMode.SYNCED and self.surfaces:
            raise ValueError("synced locality must not carry surfaces")
        return self

    def visible_to(self, surface: Provider | None) -> bool:
        """`surface=None` is a trusted internal caller (CLI, a background job, the
        compiler) -- the same "no restriction" convention `Scope`-filtering already
        uses for `scope=None`. An authenticated connector always passes a surface."""
        if self.mode is LocalityMode.SYNCED or surface is None:
            return True
        return surface in self.surfaces

    def __str__(self) -> str:
        if self.mode is LocalityMode.SYNCED:
            return "synced"
        return f"local_only:{','.join(sorted(str(s) for s in self.surfaces))}"


GLOBAL_LOCALITY = Locality()


class Provenance(BaseModel):
    """Where this object came from. Never optional -- an object with no provenance
    cannot be shown honestly in the Context Inspector, so we refuse to store one."""

    origin_type: OriginType
    provider: Provider
    source_object_ids: list[str] = Field(default_factory=list)
    confidence: Confidence = 1.0
    captured_at: datetime = Field(default_factory=_now)
    note: str | None = None


class ProviderMapping(BaseModel):
    """Identity of this object inside a destination product, once compiled there."""

    external_id: str
    external_type: str
    compiled_at: datetime = Field(default_factory=_now)


class EdgeType(StrEnum):
    SUPERSEDES = "supersedes"
    DERIVED_FROM = "derived_from"
    BELONGS_TO = "belongs_to"
    MENTIONS = "mentions"
    CONTRADICTS = "contradicts"
    RELATES_TO = "relates_to"


class Edge(BaseModel):
    model_config = ConfigDict(frozen=True)

    src_id: str
    dst_id: str
    type: EdgeType
    confidence: Confidence = 1.0
    created_at: datetime = Field(default_factory=_now)


class ContextObject(BaseModel):
    """The one row type. Subtypes narrow `type` and use `payload` for extras."""

    model_config = ConfigDict(validate_assignment=True)

    id: str = ""
    type: ObjectType
    content: str
    scope: Scope = GLOBAL_SCOPE
    locality: Locality = GLOBAL_LOCALITY
    confidence: Confidence = 1.0
    extraction_method: ExtractionMethod
    sensitivity: Sensitivity = Sensitivity.NORMAL
    supersedes: str | None = None
    provenance: Provenance
    provider_mappings: dict[Provider, ProviderMapping] = Field(default_factory=dict)

    version: int = 1
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    # Compression (§6) collapses low-confidence/superseded nodes rather than deleting
    # them; a retired object stays queryable for provenance but is excluded from
    # retrieval and from compile.
    retired_at: datetime | None = None
    ttl_days: int | None = None

    # Valid time, which is not the same axis as `created_at`. `created_at` is when we
    # *recorded* a fact; these are when it is *true in the world*. A policy effective
    # 1 April recorded on 15 March differs on both axes, and an audit that conflates
    # them answers "what did we know" when it was asked "what was in force".
    #
    # Both default to None, meaning "as far as we know, always" — which is the honest
    # reading of a preference someone stated without dating it, and keeps every
    # existing object's behaviour unchanged.
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid_interval_is_ordered(self) -> Self:
        """An interval ending before it starts is a data-entry error, not an odd
        fact. Postgres refuses it with a CHECK; refusing it here too is what keeps
        the two backends telling the same story."""
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_from >= self.valid_until
        ):
            raise ValueError("valid_from must be before valid_until")
        return self

    @model_validator(mode="after")
    def _assign_id(self) -> Self:
        if not self.id:
            # validate_assignment is on, so mutate through __dict__ to avoid recursion.
            self.__dict__["id"] = new_id(self.type)
        return self

    @property
    def is_active(self) -> bool:
        return self.retired_at is None

    def in_force_at(self, moment: datetime) -> bool:
        """Was this true in the world at `moment`?

        Half-open on purpose: `valid_until` is when a fact stopped being true, so a
        policy superseded at midnight was not in force at midnight. Closing the
        interval would make two successive policies both apply for one instant, which
        is exactly the ambiguity an auditor is trying to resolve.
        """
        if self.valid_from is not None and moment < self.valid_from:
            return False
        return not (self.valid_until is not None and moment >= self.valid_until)

    def touch(self) -> None:
        self.__dict__["version"] = self.version + 1
        self.__dict__["updated_at"] = _now()


class Memory(ContextObject):
    """SCOPE §2. Same table, same edges, same versioning as every other subtype."""

    type: Literal[ObjectType.MEMORY] = ObjectType.MEMORY
    kind: MemoryKind = MemoryKind.FACT

    @classmethod
    def from_write(
        cls,
        content: str,
        *,
        kind: MemoryKind = MemoryKind.FACT,
        scope: Scope = GLOBAL_SCOPE,
        locality: Locality = GLOBAL_LOCALITY,
        provider: Provider = Provider.LOCAL,
        extraction_method: ExtractionMethod = ExtractionMethod.MCP_LIVE_WRITE,
        origin_type: OriginType = OriginType.AGENT,
        sensitivity: Sensitivity = Sensitivity.NORMAL,
        confidence: float | None = None,
        supersedes: str | None = None,
        source_object_ids: list[str] | None = None,
    ) -> Memory:
        """The single constructor every ingest path funnels through.

        Confidence defaults from `extraction_method` (§3.1) so the connector/export
        distinction is enforced by the schema instead of by each caller remembering it.
        """
        resolved = default_confidence(extraction_method) if confidence is None else confidence
        return cls(
            content=content,
            kind=kind,
            scope=scope,
            locality=locality,
            confidence=resolved,
            extraction_method=extraction_method,
            sensitivity=sensitivity,
            supersedes=supersedes,
            provenance=Provenance(
                origin_type=origin_type,
                provider=provider,
                source_object_ids=source_object_ids or [],
                confidence=resolved,
            ),
        )


#: Subtypes that add fields beyond ContextObject. Everything absent from this map
#: round-trips through the base class -- which is the §2 discipline showing up in
#: code: a new object type costs a line here only if it genuinely needs one.
_SUBTYPES: dict[ObjectType, type[ContextObject]] = {
    ObjectType.MEMORY: Memory,
}


def object_from_record(record: dict[str, Any]) -> ContextObject:
    """Rehydrate one object from its serialized form.

    Shared by every deserialization path -- the in-process store's snapshot file,
    the Postgres row mapper, and event-log replay -- so a subtype gaining fields
    cannot be handled correctly in one place and wrongly in another.
    """
    model = _SUBTYPES.get(ObjectType(record["type"]), ContextObject)
    return model.model_validate(record)
