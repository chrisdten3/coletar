from coletar.jobs import compress
from coletar.schema import ExtractionMethod, Memory, MemoryKind, Scope, ScopeType
from coletar.store import InMemoryStore
from conftest import TENANT


async def test_put_and_get_round_trips():
    store = InMemoryStore()
    memory = Memory.from_write("Chris is based in DC.")
    await store.put_object(TENANT, memory)
    assert (await store.get_object(TENANT, memory.id)).content == "Chris is based in DC."


async def test_every_write_appends_an_event():
    """Nothing may mutate the graph without a log entry — provenance depends on it."""
    store = InMemoryStore()
    await store.put_object(TENANT, Memory.from_write("A fact."))
    assert len(await store.list_events(TENANT)) == 1


async def test_search_ranks_higher_confidence_first():
    store = InMemoryStore()
    weak = Memory.from_write(
        "Chris prefers tabs.", extraction_method=ExtractionMethod.DERIVED_SUMMARY
    )
    strong = Memory.from_write(
        "Chris prefers spaces.", extraction_method=ExtractionMethod.EXPLICIT_STATEMENT
    )
    await store.put_object(TENANT, weak)
    await store.put_object(TENANT, strong)
    results = await store.search(TENANT, "what does chris prefer")
    assert results[0].obj.id == strong.id


async def test_project_scoped_search_includes_global_and_excludes_other_projects():
    """A conversation inside a project still sees the user's global context; it
    never sees another project's. Both halves are M1.2 acceptance criteria."""
    store = InMemoryStore()
    scope = Scope(type=ScopeType.PROJECT, id="proj_a")
    mine = await store.put_object(TENANT, 
        Memory.from_write("Project A ships in March.", scope=scope)
    )
    globally = await store.put_object(TENANT, Memory.from_write("Global fact about March."))
    await store.put_object(TENANT, 
        Memory.from_write(
            "Project B ships in March.", scope=Scope(type=ScopeType.PROJECT, id="proj_b")
        )
    )

    found = {hit.obj.id for hit in await store.search(TENANT, "march", scope=scope)}

    assert found == {mine.id, globally.id}


async def test_compression_retires_superseded_but_keeps_it_readable():
    store = InMemoryStore()
    old = await store.put_object(TENANT, Memory.from_write("Chris works at Acme."))
    await store.put_object(TENANT, 
        Memory.from_write(
            "Chris works at Globex.", kind=MemoryKind.CORRECTION, supersedes=old.id
        )
    )

    report = await compress(store, TENANT)

    assert report.retired == 1
    assert not (await store.get_object(TENANT, old.id)).is_active
    # Retired, not deleted: still there for the Context Inspector.
    assert await store.get_object(TENANT, old.id) is not None
    assert all(hit.obj.id != old.id for hit in await store.search(TENANT, "chris works"))
