"""A graph shaped like a real one, for a demo you drive yourself.

`seed.py` exists to prove the schema — one object of every type, a supersedes chain.
That is the right fixture for a test and the wrong one for a room: it demonstrates
that the model is complete, not that the product is worth anything.

This builds the whiteboard scenario instead. Memories arrive the way they actually
would — some parsed out of a Claude export, some out of a ChatGPT export, some
written live by a connector, one learned by a local model — so `via claude ·
account_export_parse` on the screen is the truth about the object rather than a
label chosen to look plausible. Confidence is *not* passed anywhere below: it
defaults from `extraction_method`, so an export-derived memory is visibly less
certain than a typed connector write, which is the §3.1 distinction the Inspector
exists to show.

Three surfaces see three different graphs. That is the entire demo, and it needs
real objects to be worth watching.

**This is example data and says so.** It is not, and must not become, a substitute
for `coletar import-claude ~/Downloads` — a real import is the more convincing
demo, and the only one that proves the parsers work. This is what you show when the
screen is being recorded.
"""

from __future__ import annotations

from dataclasses import dataclass

from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    GLOBAL_LOCALITY,
    GLOBAL_SCOPE,
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

#: Locality that names exactly one surface. `Locality` refuses `LOCAL_ONLY` with no
#: surfaces, so there is no way to express "restricted to nothing" by accident.
def _only(*surfaces: Provider) -> Locality:
    return Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset(surfaces))


@dataclass(frozen=True)
class DemoMemory:
    content: str
    kind: MemoryKind
    provider: Provider
    method: ExtractionMethod
    origin: OriginType
    locality: Locality = GLOBAL_LOCALITY
    scope: Scope = GLOBAL_SCOPE


#: Ordered so the room reads top to bottom: shared preferences first, then the two
#: objects whose whole point is that some surface cannot see them.
DEMO: tuple[DemoMemory, ...] = (
    DemoMemory(
        "Prefers fixed-point arithmetic for money. Never floats.",
        MemoryKind.PREFERENCE,
        Provider.CLAUDE,
        ExtractionMethod.ACCOUNT_EXPORT_PARSE,
        OriginType.USER,
    ),
    DemoMemory(
        "Writes Python with type annotations on every public function.",
        MemoryKind.PREFERENCE,
        Provider.CHATGPT,
        ExtractionMethod.ACCOUNT_EXPORT_PARSE,
        OriginType.USER,
    ),
    DemoMemory(
        "Ledger deploys to Fly.io; Postgres runs on the Hobby tier.",
        MemoryKind.FACT,
        Provider.CHATGPT,
        ExtractionMethod.ACCOUNT_EXPORT_PARSE,
        OriginType.USER,
        scope=LEDGER,
    ),
    DemoMemory(
        "Decided against a second table for the queue; pending is a payload flag.",
        MemoryKind.FACT,
        Provider.CLAUDE,
        ExtractionMethod.MCP_LIVE_WRITE,
        OriginType.AGENT,
        scope=LEDGER,
    ),
    # The two that make the switcher worth watching.
    DemoMemory(
        "Handling the Northwind litigation matter; filings are due 14 November.",
        MemoryKind.FACT,
        Provider.CLAUDE,
        ExtractionMethod.MCP_LIVE_WRITE,
        OriginType.USER,
        locality=_only(Provider.CLAUDE),
    ),
    DemoMemory(
        "Salary band for the new backend hire is 68-74k.",
        MemoryKind.FACT,
        Provider.LOCAL,
        ExtractionMethod.EXPLICIT_STATEMENT,
        OriginType.USER,
        locality=_only(Provider.LOCAL),
    ),
)


async def seed_demo(store: Store, tenant_id: TenantId) -> list[str]:
    """Write the demo graph. Returns the ids, newest last.

    Every write carries its event, like every other write path — a seeded object
    with no event would be exactly the silent provenance gap constraint 5 forbids,
    and the Inspector's own timeline would have nothing to show for these.
    """
    written: list[str] = []
    for item in DEMO:
        memory = Memory.from_write(
            item.content,
            kind=item.kind,
            scope=item.scope,
            locality=item.locality,
            provider=item.provider,
            extraction_method=item.method,
            origin_type=item.origin,
        )
        stored = await store.put_object(
            tenant_id,
            memory,
            event=Event(
                type=EventType.OBJECT_CREATED,
                object_id=memory.id,
                actor=Actor.SYSTEM,
                provider=item.provider,
                detail={"demo_seed": True},
            ),
        )
        written.append(stored.id)
    return written
