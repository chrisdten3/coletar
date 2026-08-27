from coletar.jobs import compress
from coletar.schema import ExtractionMethod, Memory, MemoryKind, Scope, ScopeType
from coletar.store import InMemoryStore


async def test_put_and_get_round_trips():
    store = InMemoryStore()
    memory = Memory.from_write("Chris is based in DC.")
    await store.put_object(memory)
    assert (await store.get_object(memory.id)).content == "Chris is based in DC."


async def test_every_write_appends_an_event():
    """Nothing may mutate the graph without a log entry — provenance depends on it."""
    store = InMemoryStore()
    await store.put_object(Memory.from_write("A fact."))
    assert len(await store.list_events()) == 1


async def test_search_ranks_higher_confidence_first():
    store = InMemoryStore()
    weak = Memory.from_write(
        "Chris prefers tabs.", extraction_method=ExtractionMethod.DERIVED_SUMMARY
    )
    strong = Memory.from_write(
        "Chris prefers spaces.", extraction_method=ExtractionMethod.EXPLICIT_STATEMENT
    )
    await store.put_object(weak)
    await store.put_object(strong)
    results = await store.search("what does chris prefer")
    assert results[0][0].id == strong.id


async def test_search_respects_scope():
    store = InMemoryStore()
    scope = Scope(type=ScopeType.PROJECT, id="proj_a")
    await store.put_object(Memory.from_write("Project A ships in March.", scope=scope))
    await store.put_object(Memory.from_write("Global fact about March."))
    assert len(await store.search("march", scope=scope)) == 1


async def test_compression_retires_superseded_but_keeps_it_readable():
    store = InMemoryStore()
    old = await store.put_object(Memory.from_write("Chris works at Acme."))
    await store.put_object(
        Memory.from_write(
            "Chris works at Globex.", kind=MemoryKind.CORRECTION, supersedes=old.id
        )
    )

    report = await compress(store)

    assert report.retired == 1
    assert not (await store.get_object(old.id)).is_active
    # Retired, not deleted: still there for the Context Inspector.
    assert await store.get_object(old.id) is not None
    assert all(o.id != old.id for o, _ in await store.search("chris works"))
