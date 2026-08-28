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
