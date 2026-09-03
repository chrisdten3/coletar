"""What a model is allowed to propose, and what only coletar may assign (§2, §3.1).

Structured outputs mean the model fills in a Pydantic schema directly rather than
emitting JSON we parse and hope about. That makes the schema the enforcement point,
which is the reason this module exists separately from `schema/objects.py`: the
shape a model may return is deliberately **narrower** than the shape of a stored
object.

A model may propose content, a kind, and the *names* of people and things. It may
not propose an id, a confidence, a locality, a scope, or a provenance record. Those
are assigned here, from the extraction method and the calling surface. The reason is
AGENTS.md §7 in its sharpest form: the transcript being mined was written by models
and, transitively, by whatever those models read, and it is about to be handed to
another model. A prompt-injected instruction cannot talk its way into a higher
confidence or a wider locality if the schema has nowhere to put one.

The same argument applies to identity. A proposal names an entity ("Amanda"); this
module resolves that name to an object and creates the edge. A model permitted to
emit object ids could attach a fabricated fact to any object already in the graph.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from coletar.schema.objects import (
    GLOBAL_LOCALITY,
    GLOBAL_SCOPE,
    ContextObject,
    Edge,
    EdgeType,
    ExtractionMethod,
    Locality,
    Memory,
    MemoryKind,
    ObjectType,
    OriginType,
    Provenance,
    Provider,
    Scope,
    default_confidence,
)

#: Entity names longer than this are a pasted sentence that slipped the guards, not
#: a name. Cheap to check, and it keeps a mangled proposal from becoming a node.
MAX_NAME_CHARS = 80


class ProposedMemory(BaseModel):
    """A durable first-person fact about the user, in the user's own words."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(description="The statement, in the user's own phrasing.")
    kind: MemoryKind = Field(description="fact, preference, instruction, goal or correction.")


class ProposedEntity(BaseModel):
    """A person, organisation or thing the user's world contains.

    Not a memory: an entity is not a claim about the user, so it never reaches the
    user's own profile. This is what the Walleye case needs — "Amanda, Walleye
    Business Development" is real and worth keeping, and asserting it *of the user*
    is the bug that made this module necessary.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="What the person or organisation is called.")
    content: str = Field(description="One line identifying them, from the user's words.")


class ProposedFact(BaseModel):
    """Something true about the user that involves one or more entities."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(description="The fact, stated from the user's perspective.")
    about: list[str] = Field(
        default_factory=list,
        description="Names of entities this fact involves. Must match a proposed entity.",
    )


class Proposal(BaseModel):
    """One model response. Empty lists are a good answer and the common one."""

    model_config = ConfigDict(extra="forbid")

    memories: list[ProposedMemory] = Field(default_factory=list)
    entities: list[ProposedEntity] = Field(default_factory=list)
    facts: list[ProposedFact] = Field(default_factory=list)


def _entity(
    proposed: ProposedEntity,
    *,
    extraction_method: ExtractionMethod,
    provider: Provider,
    scope: Scope,
    locality: Locality,
) -> ContextObject:
    """An entity object. No subclass: it adds no fields over ContextObject, and §2
    says a type costs a line in `_SUBTYPES` only when it genuinely needs one. The
    name lives in `payload`, which is where subtype-specific data belongs."""
    confidence = default_confidence(extraction_method)
    return ContextObject(
        type=ObjectType.ENTITY,
        content=proposed.content,
        scope=scope,
        locality=locality,
        confidence=confidence,
        extraction_method=extraction_method,
        provenance=Provenance(
            origin_type=OriginType.USER,
            provider=provider,
            source_object_ids=[],
            confidence=confidence,
        ),
        payload={"name": proposed.name},
    )


def materialise(
    proposal: Proposal,
    *,
    extraction_method: ExtractionMethod,
    provider: Provider = Provider.LOCAL,
    scope: Scope = GLOBAL_SCOPE,
    locality: Locality = GLOBAL_LOCALITY,
) -> tuple[list[ContextObject], list[Edge]]:
    """Turn one proposal into objects and edges, assigning everything the model
    could not.

    A fact naming an entity the same proposal did not propose is dropped rather than
    linked to nothing or to a guessed match against the existing graph. A dangling
    reference here would be indistinguishable, later, from a fact about someone the
    Inspector cannot name.
    """
    objects: list[ContextObject] = []
    edges: list[Edge] = []

    for memory in proposal.memories:
        objects.append(
            Memory.from_write(
                content=memory.content,
                kind=memory.kind,
                scope=scope,
                locality=locality,
                provider=provider,
                extraction_method=extraction_method,
                origin_type=OriginType.USER,
            )
        )

    by_name: dict[str, str] = {}
    for entity in proposal.entities:
        if not entity.name.strip() or len(entity.name) > MAX_NAME_CHARS:
            continue
        obj = _entity(
            entity,
            extraction_method=extraction_method,
            provider=provider,
            scope=scope,
            locality=locality,
        )
        objects.append(obj)
        by_name[entity.name.strip().casefold()] = obj.id

    confidence = default_confidence(extraction_method)
    for fact in proposal.facts:
        targets = [
            by_name[name.strip().casefold()]
            for name in fact.about
            if name.strip().casefold() in by_name
        ]
        if fact.about and not targets:
            continue
        obj = ContextObject(
            type=ObjectType.FACT,
            content=fact.content,
            scope=scope,
            locality=locality,
            confidence=confidence,
            extraction_method=extraction_method,
            provenance=Provenance(
                origin_type=OriginType.USER,
                provider=provider,
                source_object_ids=[],
                confidence=confidence,
            ),
        )
        objects.append(obj)
        edges.extend(
            Edge(src_id=obj.id, dst_id=target, type=EdgeType.MENTIONS) for target in targets
        )

    return objects, edges
