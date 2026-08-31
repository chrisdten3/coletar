"""The ingest boundary (SCOPE §5, §4.1).

Every path that turns *observed* text into stored objects goes through here: the
local proxy's extraction, the MCP connector's `write_memory`, and later the export
parser. What they share is that the same fact will arrive more than once — a user
states a preference in January and again in March, or the proxy sees it in two
conversations — and the graph should not grow a copy each time.

**Why this is not in `Store`.** A compiler, a migration job or a replay must be able
to write an exact object without an ingest policy quietly interfering. `put_object`
stays a faithful put; deduplication is a property of *ingestion*, so it lives at the
boundary the observing paths share and nowhere else.

**Why it matters more than it looks.** Near-duplicates are already dropped at
assembly time, which protects retrieval — the model never sees the same fact twice.
It does not protect the compiler: a compile reads `list_objects`, so True Migration
would faithfully emit every duplicate the proxy ever wrote, into a destination with
finite context. Read-time deduplication hides the problem from the place that can
tolerate it and leaves it in the place that cannot.

**A duplicate corroborates; it does not vanish.** That the user said something again,
in a different session, is real provenance. Dropping it silently throws that away.
So the existing object gets an `object.corroborated` event and a refreshed
`updated_at`, and its confidence is deliberately left alone -- repetition is weak
evidence, and inflating a score on it is arithmetic nobody asked for.
"""

from __future__ import annotations

from dataclasses import dataclass

from coletar.retrieval.context import NEAR_DUPLICATE_THRESHOLD
from coletar.retrieval.embedding import tokenize
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import ContextObject, Memory, Provider, Scope
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: How many candidates to consider. Deduplication only needs the nearest few: if the
#: same fact is not in the top handful for its own text, it is not a duplicate.
_CANDIDATES = 8


@dataclass(frozen=True)
class IngestResult:
    """What ingestion did, so a caller can say so rather than guess."""

    object_id: str
    created: bool
    #: Set when the write was folded into an object that already said this.
    corroborated: str | None = None

    @property
    def stored(self) -> bool:
        return self.created


def is_near_duplicate(a: str, b: str) -> bool:
    """The same notion of sameness the assembly stage uses, so a fact judged a
    duplicate at read time is judged a duplicate at write time."""
    left, right = set(tokenize(a)), set(tokenize(b))
    if not left or not right:
        return False
    return len(left & right) / min(len(left), len(right)) >= NEAR_DUPLICATE_THRESHOLD


async def find_duplicate(
    store: Store,
    tenant_id: TenantId,
    content: str,
    *,
    scope: Scope | None = None,
    caller_surface: Provider | None = None,
) -> ContextObject | None:
    """The context lookup that happens *before* a write.

    `caller_surface` matters here for the same reason it matters on every other
    read path: without it, a write from surface A could silently corroborate an
    object that is `local_only` to surface B, leaving the fact invisible on the very
    surface that just "wrote" it. Filtering the duplicate search the same way a
    normal read would keeps a write's own next read consistent with it.
    """
    for hit in await store.search(
        tenant_id, content, scope=scope, caller_surface=caller_surface, top_k=_CANDIDATES
    ):
        if is_near_duplicate(content, hit.obj.content):
            return hit.obj
    return None


async def remember(
    store: Store,
    tenant_id: TenantId,
    memory: Memory,
    *,
    event: Event | None = None,
    dedup: bool = True,
    caller_surface: Provider | None = None,
) -> IngestResult:
    """Store one observed memory, folding it into an existing object if it repeats.

    `dedup=False` exists for paths that must write exactly what they were given --
    a replay, or a test asserting about storage rather than about ingestion. It is
    not the default, because every path that observes text should be deduplicating.
    """
    if dedup:
        # A correction is *supposed* to resemble what it corrects, so it must never
        # be folded into it -- that would silently discard the correction and leave
        # the stale fact standing.
        existing = (
            None
            if memory.supersedes is not None
            else await find_duplicate(
                store, tenant_id, memory.content, scope=memory.scope,
                caller_surface=caller_surface,
            )
        )
        if existing is not None:
            await store.append_event(
                tenant_id,
                Event(
                    type=EventType.OBJECT_CORROBORATED,
                    object_id=existing.id,
                    actor=(event.actor if event else Actor.SYSTEM),
                    provider=memory.provenance.provider,
                    detail={
                        "restated_as": memory.content,
                        "extraction_method": memory.extraction_method,
                    },
                ),
            )
            return IngestResult(object_id=existing.id, created=False, corroborated=existing.id)

    stored = await store.put_object(tenant_id, memory, event=event)
    return IngestResult(object_id=stored.id, created=True)
