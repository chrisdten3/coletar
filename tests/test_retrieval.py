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
