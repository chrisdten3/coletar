"""What a model may propose, and what it may not (§2, §3.1, §7).

The narrowing is the point. These tests assert the *absence* of fields as much as
the presence of them: a schema a prompt cannot talk past is the only guard that
survives the transcript being adversarial.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from coletar.extraction.proposal import (
    Proposal,
    ProposedEntity,
    ProposedFact,
    ProposedMemory,
    materialise,
)
from coletar.schema.objects import (
    EdgeType,
    ExtractionMethod,
    LocalityMode,
    MemoryKind,
    ObjectType,
    default_confidence,
)

EXPORT = ExtractionMethod.ACCOUNT_EXPORT_PARSE


def test_a_third_party_becomes_an_entity_not_a_fact_about_the_user() -> None:
    """The failure this module exists for. A recruiter's introduction produced
    "My name is Amanda and I'm on Walleye's Business Development team" as a
    first-person fact about the account holder. The right shape keeps the person
    and the user's relationship to them, and asserts neither of the user."""
    objects, edges = materialise(
        Proposal(
            entities=[
                ProposedEntity(name="Amanda", content="Amanda, Walleye Business Development")
            ],
            facts=[
                ProposedFact(
                    content="Had a call with Amanda about a quant dev internship",
                    about=["Amanda"],
                )
            ],
        ),
        extraction_method=EXPORT,
    )
    kinds = {o.type for o in objects}
    assert kinds == {ObjectType.ENTITY, ObjectType.FACT}
    assert ObjectType.MEMORY not in kinds, "a third party is never a fact about the user"

    entity = next(o for o in objects if o.type is ObjectType.ENTITY)
    fact = next(o for o in objects if o.type is ObjectType.FACT)
    assert entity.payload["name"] == "Amanda"
    assert [(e.src_id, e.dst_id, e.type) for e in edges] == [
        (fact.id, entity.id, EdgeType.MENTIONS)
    ]


def test_a_fact_naming_an_unproposed_entity_is_dropped() -> None:
    """Better no fact than one linked to nothing, or guessed against the graph —
    a dangling reference is indistinguishable later from a fact about someone the
    Inspector cannot name."""
    objects, edges = materialise(
        Proposal(facts=[ProposedFact(content="Met with Dana", about=["Dana"])]),
        extraction_method=EXPORT,
    )
    assert objects == []
    assert edges == []


def test_a_fact_about_nobody_in_particular_still_lands() -> None:
    objects, _ = materialise(
        Proposal(facts=[ProposedFact(content="The Series A closed in March")]),
        extraction_method=EXPORT,
    )
    assert [o.type for o in objects] == [ObjectType.FACT]


@pytest.mark.parametrize("field", ["confidence", "locality", "id", "provenance", "scope"])
def test_a_model_cannot_propose_the_fields_that_would_corrupt_provenance(field: str) -> None:
    """§7: the transcript is written by models and read by one. A prompt-injected
    instruction has nowhere to put a higher confidence or a wider locality."""
    with pytest.raises(ValidationError):
        ProposedMemory(content="I prefer tabs", kind=MemoryKind.PREFERENCE, **{field: "anything"})


def test_confidence_comes_from_the_extraction_method_not_the_model() -> None:
    """§3.1's table, enforced by the constructor rather than by each caller."""
    objects, _ = materialise(
        Proposal(memories=[ProposedMemory(content="I prefer tabs", kind=MemoryKind.PREFERENCE)]),
        extraction_method=EXPORT,
    )
    assert objects[0].confidence == default_confidence(EXPORT)


def test_an_entity_name_long_enough_to_be_a_sentence_is_refused() -> None:
    objects, _ = materialise(
        Proposal(entities=[ProposedEntity(name="x" * 200, content="a pasted paragraph")]),
        extraction_method=EXPORT,
    )
    assert objects == []


def test_finding_nothing_is_a_valid_answer() -> None:
    objects, edges = materialise(Proposal(), extraction_method=EXPORT)
    assert (objects, edges) == ([], [])


def test_locality_is_assigned_by_the_caller_not_the_proposal() -> None:
    """A memory's readable surfaces are the calling connector's business."""
    objects, _ = materialise(
        Proposal(memories=[ProposedMemory(content="I prefer tabs", kind=MemoryKind.PREFERENCE)]),
        extraction_method=EXPORT,
    )
    assert objects[0].locality.mode is LocalityMode.SYNCED


# --- end to end: the entity reaches the store and the Inspector explains it -------


@pytest.mark.asyncio
async def test_an_entity_survives_to_the_inspector_with_its_reason(tmp_path) -> None:
    """Constraint 4 in one test. An entity the Inspector cannot explain should not
    exist, so the chain that matters is: extraction names a person, the store keeps
    the edge, and the view can say why she is there — in the user's own words."""
    from coletar.inspector.metrics import build_agentic_view
    from coletar.schema.events import Actor, Event, EventType
    from coletar.schema.objects import Provider
    from coletar.store.memory import InMemoryStore
    from conftest import TENANT

    objects, edges = materialise(
        Proposal(
            entities=[
                ProposedEntity(name="Amanda", content="Amanda, Walleye Business Development")
            ],
            facts=[
                ProposedFact(
                    content="Had a call with Amanda about a quant dev internship",
                    about=["Amanda"],
                )
            ],
        ),
        extraction_method=EXPORT,
    )

    store = InMemoryStore()
    for obj in objects:
        await store.put_object(
            TENANT,
            obj,
            event=Event(
                type=EventType.CONNECTOR_WRITE,
                object_id=obj.id,
                actor=Actor.MIGRATION,
                provider=Provider.CHATGPT,
            ),
        )
    for edge in edges:
        await store.add_edge(TENANT, edge)

    view = await build_agentic_view(store, TENANT)
    entity = next(o for o in objects if o.type is ObjectType.ENTITY)

    explanation = view.mentioned_by.get(entity.id, [])
    assert [f.content for f in explanation] == [
        "Had a call with Amanda about a quant dev internship"
    ], "an entity with no stated reason is one the user cannot judge"
