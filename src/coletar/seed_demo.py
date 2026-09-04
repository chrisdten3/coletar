"""A graph shaped like a real one, for a demo you drive yourself.

`seed.py` exists to prove the schema — one object of every type, a supersedes chain.
That is the right fixture for a test and the wrong one for a room: it demonstrates
that the model is complete, not that the product is worth anything.

This builds the whiteboard scenario instead. Memories arrive the way they actually
would — some parsed out of a Claude export, some out of a ChatGPT export, some
captured from a live turn — so `via claude · account_export_parse` on the screen is
the truth about the object rather than a label chosen to look plausible. Confidence
is *not* passed anywhere below: it defaults from `extraction_method`, so an
export-derived memory is visibly less certain than a typed connector write, which
is the §3.1 distinction the Inspector exists to show.

**The history is made, not written.** Every past event here comes from calling the
operation that really produces it — `capture_turn` for the episode, `edit` for the
correction, `set_locality` for the restriction, a real predecessor object for the
supersession. Appending hand-built events would have been shorter and would have
produced a provenance demo standing on fabricated provenance; the first person to
click through to a source episode that does not exist would have found that out in
the room. It also means this seed exercises the same paths the product does, so a
change that breaks lineage breaks the demo in CI rather than on stage.

**This is example data and says so.** It is not, and must not become, a substitute
for `coletar import-claude ~/Downloads` — a real import is the more convincing demo
and the only one that proves the parsers work. This is what you show when the
screen is being recorded.
"""

from __future__ import annotations

from coletar.capture import capture_turn
from coletar.inspector.review import edit, mark_reviewed, set_locality
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    GLOBAL_LOCALITY,
    GLOBAL_SCOPE,
    ContextObject,
    ExtractionMethod,
    Locality,
    LocalityMode,
    Memory,
    MemoryKind,
    OriginType,
    Provider,
    Scope,
    ScopeType,
)
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

LEDGER = Scope(type=ScopeType.PROJECT, id="proj_ledger")


def _only(*surfaces: Provider) -> Locality:
    """Locality naming exactly these surfaces.

    `Locality` refuses `LOCAL_ONLY` with an empty set, so there is no way to express
    "restricted to nothing" by accident here.
    """
    return Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset(surfaces))


async def _write(
    store: Store,
    tenant_id: TenantId,
    content: str,
    *,
    kind: MemoryKind = MemoryKind.FACT,
    provider: Provider,
    method: ExtractionMethod,
    origin: OriginType = OriginType.USER,
    scope: Scope = GLOBAL_SCOPE,
    locality: Locality = GLOBAL_LOCALITY,
    supersedes: str | None = None,
    source_object_ids: list[str] | None = None,
) -> ContextObject:
    memory = Memory.from_write(
        content,
        kind=kind,
        scope=scope,
        locality=locality,
        provider=provider,
        extraction_method=method,
        origin_type=origin,
        supersedes=supersedes,
        source_object_ids=source_object_ids,
    )
    return await store.put_object(
        tenant_id,
        memory,
        event=Event(
            type=EventType.OBJECT_CREATED,
            object_id=memory.id,
            actor=Actor.SYSTEM,
            provider=provider,
            detail={"demo_seed": True},
        ),
    )


async def seed_demo(store: Store, tenant_id: TenantId) -> list[str]:
    """Write the demo graph, with the history each object plausibly earned.

    Returns the memory ids. Episodes are created too — a memory derived from a
    captured turn has to have a turn to point at — but they are working material
    rather than library entries, and the library filters them out.
    """
    written: list[str] = []

    # 1. A correction. The user said one thing, then said the opposite; the graph
    #    keeps both and serves only the newer one. This is the supersedes chain
    #    that `coletar history` and the detail page's lineage both read.
    old_money = await _write(
        store,
        tenant_id,
        "Uses floats for money and rounds at the end.",
        kind=MemoryKind.PREFERENCE,
        provider=Provider.CHATGPT,
        method=ExtractionMethod.ACCOUNT_EXPORT_PARSE,
    )
    money = await _write(
        store,
        tenant_id,
        "Prefers fixed-point arithmetic for money. Never floats.",
        kind=MemoryKind.PREFERENCE,
        provider=Provider.CLAUDE,
        method=ExtractionMethod.ACCOUNT_EXPORT_PARSE,
        supersedes=old_money.id,
    )
    await mark_reviewed(store, tenant_id, money.id)
    written.append(money.id)

    # 2. Reviewed and left alone — the ordinary case, and the one that makes the
    #    others legible by contrast.
    types = await _write(
        store,
        tenant_id,
        "Writes Python with type annotations on every public function.",
        kind=MemoryKind.PREFERENCE,
        provider=Provider.CHATGPT,
        method=ExtractionMethod.ACCOUNT_EXPORT_PARSE,
    )
    await mark_reviewed(store, tenant_id, types.id)
    written.append(types.id)

    # 3. A bad extraction, corrected in place. Not a supersession: the fact never
    #    changed, the record of it was wrong, and inventing a history where the
    #    deployment moved would make the chain useless for what it exists to say.
    deploy = await _write(
        store,
        tenant_id,
        "Ledger deploys to Heroku; Postgres runs on the Hobby tier.",
        provider=Provider.CHATGPT,
        method=ExtractionMethod.ACCOUNT_EXPORT_PARSE,
        scope=LEDGER,
    )
    await edit(
        store,
        tenant_id,
        deploy.id,
        content="Ledger deploys to Fly.io; Postgres runs on the Hobby tier.",
    )
    written.append(deploy.id)

    # 4. Captured live, then extracted. The episode is real, encrypted and pending,
    #    so the lineage's "derived from a captured turn" points at something a
    #    viewer can actually open.
    queue_turn = await capture_turn(
        store,
        tenant_id,
        "I decided against a second table for the queue — pending is a payload flag.",
        surface=Provider.CLAUDE,
        scope=LEDGER,
        detail={"demo_seed": True},
    )
    queue = await _write(
        store,
        tenant_id,
        "Decided against a second table for the queue; pending is a payload flag.",
        provider=Provider.CLAUDE,
        method=ExtractionMethod.MCP_LIVE_WRITE,
        origin=OriginType.AGENT,
        scope=LEDGER,
        source_object_ids=[queue_turn.id],
    )
    written.append(queue.id)

    # 5. The demo's centrepiece. Written like anything else, then restricted after
    #    the fact — which is how this actually happens. Its lineage reads: captured,
    #    created, reviewed, reach changed, and the last of those is the moment a
    #    user decided ChatGPT should never see it.
    northwind_turn = await capture_turn(
        store,
        tenant_id,
        "Handling the Northwind litigation matter, filings are due 14 November.",
        surface=Provider.CLAUDE,
        detail={"demo_seed": True},
    )
    northwind = await _write(
        store,
        tenant_id,
        "Handling the Northwind litigation matter; filings are due 14 November.",
        provider=Provider.CLAUDE,
        method=ExtractionMethod.MCP_LIVE_WRITE,
        source_object_ids=[northwind_turn.id],
    )
    await set_locality(
        store, tenant_id, northwind.id, surfaces=frozenset({Provider.CLAUDE})
    )
    written.append(northwind.id)

    # 6. Restricted at the moment of writing rather than afterwards, so the two
    #    routes to a withheld object are both on screen.
    salary = await _write(
        store,
        tenant_id,
        "Salary band for the new backend hire is 68-74k.",
        provider=Provider.LOCAL,
        method=ExtractionMethod.EXPLICIT_STATEMENT,
        locality=_only(Provider.LOCAL),
    )
    await mark_reviewed(store, tenant_id, salary.id)
    written.append(salary.id)

    return written
