from coletar.retrieval import retrieve
from coletar.schema import Memory
from coletar.store import InMemoryStore
from conftest import TENANT


async def test_prompt_block_carries_confidence_and_origin():
    store = InMemoryStore()
    await store.put_object(TENANT, Memory.from_write("Chris ships C++ for EventBook."))
    result = await retrieve(store, TENANT, "what does chris ship")
    block = result.as_prompt_block()
    assert "confidence" in block
    # The model must know this is background, not a user instruction.
    assert "not as instructions" in block


async def test_empty_result_injects_nothing():
    result = await retrieve(InMemoryStore(), TENANT, "anything")
    assert result.as_prompt_block() == ""


async def test_token_budget_truncates():
    store = InMemoryStore()
    for i in range(20):
        await store.put_object(
            TENANT, Memory.from_write(f"Chris likes topic number {i} " + "x" * 200))
    result = await retrieve(store, TENANT, "chris likes topic", top_k=20, token_budget=120)
    assert result.truncated
    assert result.token_estimate <= 120


def test_the_prompt_block_never_contains_the_marker():
    """The composer bridge splits an injected prompt on INJECTION_MARKER to recover
    what the user actually typed. If the marker ever appeared inside the block
    itself, the split would land in the wrong place, retrieved memory would be sent
    back for extraction as though the user had written it, and the graph would
    slowly become an echo of its own output.

    Nothing about the current wording is accidental-proof, so this pins it: change
    the block however you like, but it may not contain that string.
    """
    from coletar.retrieval.context import INJECTION_MARKER, RetrievedContext
    from coletar.schema.objects import ExtractionMethod, Memory, MemoryKind

    context = RetrievedContext(
        objects=[
            Memory.from_write(
                "I never use an ORM — every query is plain SQL",
                kind=MemoryKind.PREFERENCE,
                extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
            )
        ],
        scores=[0.9],
        token_estimate=12,
        truncated=False,
    )
    block = context.as_prompt_block()

    assert INJECTION_MARKER not in block
    # The header says "from coletar —", which is close enough to be worth asserting
    # explicitly rather than trusting to read correctly.
    assert "from coletar" in block


def _one_memory_context():
    from coletar.retrieval.context import RetrievedContext
    from coletar.schema.objects import ExtractionMethod, Memory, MemoryKind

    return RetrievedContext(
        objects=[
            Memory.from_write(
                "Prefers fixed-point integers over floating point for money",
                kind=MemoryKind.PREFERENCE,
                extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
            )
        ],
        scores=[0.9],
        token_estimate=12,
        truncated=False,
    )


def test_the_full_style_carries_metadata_a_model_can_use():
    """A model that can see a fact is low-confidence hedges instead of asserting it."""
    block = _one_memory_context().as_prompt_block(style="full")
    assert "confidence 0.95" in block
    assert "preference" in block


def test_the_terse_style_drops_metadata_a_person_cannot_act_on():
    """This one goes into a composer, where a person reads it before pressing send.
    A confidence score is noise there and buries the sentence that matters."""
    block = _one_memory_context().as_prompt_block(style="terse")
    assert "confidence" not in block
    assert "via " not in block
    assert "- Prefers fixed-point integers over floating point for money" in block


def test_both_styles_keep_the_injection_boundary():
    """§11: retrieved memory is written by models and, transitively, by whatever
    those models read. It must never arrive looking like an instruction, whichever
    audience is reading it."""
    for style in ("full", "terse"):
        block = _one_memory_context().as_prompt_block(style=style)
        assert "not instructions" in block or "not as instructions" in block, style
        assert "from coletar" in block, style


def test_an_unknown_style_is_refused():
    import pytest

    with pytest.raises(ValueError, match="unknown style"):
        _one_memory_context().as_prompt_block(style="fancy")
