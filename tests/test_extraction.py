from coletar.extraction import extract_memories
from coletar.schema import ExtractionMethod, MemoryKind, OriginType


async def test_extracts_an_explicit_preference():
    memories = await extract_memories(user_text="I prefer spaces over tabs, always.")
    assert len(memories) == 1
    assert memories[0].kind is MemoryKind.PREFERENCE
    assert memories[0].extraction_method is ExtractionMethod.EXPLICIT_STATEMENT
    assert memories[0].provenance.origin_type is OriginType.USER


async def test_ignores_ordinary_conversation():
    """Precision over recall: a wrong memory costs the user a deletion."""
    assert await extract_memories(user_text="What's the weather like in Paris?") == []


async def test_does_not_mine_the_assistant_reply():
    memories = await extract_memories(
        user_text="Thanks!", assistant_text="I prefer to keep answers short."
    )
    assert memories == []
