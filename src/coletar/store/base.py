"""Store protocol.

Deliberately narrow: the MCP server, the local proxy, the compression job and the
compiler all go through this and nothing else. Swapping Postgres+pgvector for
something else is then an implementation choice, not a scope change (§5).

**Every method takes `tenant_id`, and none of them defaults it.** The Store never
assumes a tenant; only application boundaries resolve one. A default here is how a
future tool or background job falls into a shared graph without anyone noticing, and
in a product whose premise is that you own your context, that failure is silent. The
call sites are noisier for it, and the noise is the feature: every one of them names
the tenant out loud.

Three vocabulary points every implementation has to honour identically, because the
whole product reads through this interface:

**Tenant.** Object ids stay globally unique as generated, so logs and migration
manifests are unambiguous, but identity is the pair `(tenant_id, id)`. Knowing an id
grants nothing. Every read path filters, including the ones it is easy to forget:
`get_object`, `edges_from`/`edges_to`, and `list_events` -- the last of which is the
worst leak available, since event rows carry full before/after object state and so
would leak *content* rather than merely ids.

**Active.** An object is active when nothing has retired it *and* nothing newer
supersedes it. Both halves matter. Compression (§6) retires superseded objects
eventually, but retrieval must not serve a stale fact in the window before the job
runs -- so supersession excludes an object from retrieval the moment the correction
is written, not the moment the job next happens to run.

**Scope.** `search` takes the scope a *conversation* is happening in, so a project
scope means "this project's objects and everything global", never "this project's
objects only" -- a user's global preferences do not stop applying because they opened
a project. `list_objects` is the opposite: an exact filter, because
`get_project_state` has to be able to answer "what is in this container". Scopes live
*inside* a tenant, so two tenants may both hold a project called `proj_ledger`
without any relationship between them.

**Sensitivity.** `search` excludes `RESTRICTED` objects by default. This is the
§5.1 policy filter that both `retrieval/context.py` and `retrieval/ranking.py` have
always documented, and until M4.1 neither backend implemented -- a restricted memory
was returned by `retrieve()` and injected into prompts. `include_restricted=True` is
for the Context Inspector, which exists to show a user their whole graph.

**Supersession.** A superseded object stays a *candidate* and is redirected to the
object that replaced it, which is what gets scored and returned. The stale object is
never handed back. This is a recall mechanism: a correction rarely repeats the value
it corrects, so "is Chris still at Acme?" matches only the sentence being retired.

**Locality.** Independent of scope: `Locality` on the object decides which connected
*surfaces* may read it back, not which project it belongs to. `search`,
`list_objects` and `get_object` all take `caller_surface`, filtered the same way
`scope` is -- an object whose locality is `local_only` and does not name the calling
surface is invisible to it, exactly as if it belonged to another tenant. `None` means
a trusted internal caller (the CLI, a background job, the compiler) and applies no
restriction, the same convention `scope=None` already uses. An authenticated
connector always passes its surface; nothing in this protocol infers one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from coletar.retrieval.ranking import Scored
from coletar.schema.events import Event
from coletar.schema.objects import ContextObject, Edge, ObjectType, Provider, Scope
from coletar.schema.tenancy import TenantId


@runtime_checkable
class Store(Protocol):
    @property
    def embedder_model(self) -> str:
        """Which embedder produced this store's vectors.

        The retrieval trace records it, because a measured result that cannot be
        attributed to the model that produced it is not reproducible.
        """
        ...

    async def put_object(
        self, tenant_id: TenantId, obj: ContextObject, *, event: Event | None = None
    ) -> ContextObject:
        """Insert or update one object and append the matching event, atomically.

        The event carries the full before/after state. Implementations must not
        expose a path that writes the object without the event -- a row that exists
        with no event is a provenance failure we cannot detect after the fact.

        Embedding happens here, on the write path, so an object is searchable as
        soon as it is stored. A `supersedes` pointing outside `tenant_id` is
        rejected: a correction may only correct something its own tenant owns.
        """
        ...

    async def put_object_key(
        self, tenant_id: TenantId, object_id: str, key: bytes
    ) -> None:
        """Store an opaque per-object content key outside the event-snapshotted graph.

        Used only for encrypted raw episodes. The key may be hard-deleted even though
        graph objects may not: destroying it is the GDPR erasure mechanism described
        in AGENTS.md, and leaves ciphertext plus the event chain intact.
        """
        ...

    async def get_object_key(self, tenant_id: TenantId, object_id: str) -> bytes | None: ...

    async def shred_object_key(
        self, tenant_id: TenantId, object_id: str, *, reason: str
    ) -> bool:
        """Destroy one content key and append an `object.shredded` audit event."""
        ...

    async def get_object(
        self, tenant_id: TenantId, object_id: str, *, caller_surface: Provider | None = None
    ) -> ContextObject | None:
        """None when the object does not exist, belongs to another tenant, *or* is
        `local_only` to a surface other than `caller_surface`. All three are
        deliberately indistinguishable to the caller."""
        ...

    async def list_objects(
        self,
        tenant_id: TenantId,
        *,
        type: ObjectType | None = None,
        scope: Scope | None = None,
        caller_surface: Provider | None = None,
        include_retired: bool = False,
        include_superseded: bool = False,
        limit: int = 200,
    ) -> list[ContextObject]:
        """Exact-scope listing. Defaults to active objects only."""
        ...

    async def retire_object(self, tenant_id: TenantId, object_id: str, *, reason: str) -> None:
        """Soft-retire: excluded from retrieval and compile, still readable for
        provenance. The graph never hard-deletes on its own. A no-op when the object
        belongs to another tenant."""
        ...

    async def find_entity(self, tenant_id: TenantId, name: str) -> ContextObject | None:
        """The active entity with this name, or None.

        Narrow rather than a general payload query on purpose: entity-by-name is the
        one lookup the graph actually needs, and a generic `find_by_payload` would
        be an unindexed scan wearing a helpful name.

        Matching is casefolded, which merges two different Amandas. That is the
        conservative direction — a merged entity is visible and separable in the
        Inspector, a thousand duplicates are neither.
        """
        ...

    async def add_edge(self, tenant_id: TenantId, edge: Edge) -> None:
        """Idempotent on (src_id, dst_id, type) within a tenant. Re-asserting an edge
        is not a second edge and does not append a second event. Both endpoints must
        belong to `tenant_id`."""
        ...

    async def edges_from(self, tenant_id: TenantId, object_id: str) -> list[Edge]: ...

    async def edges_to(self, tenant_id: TenantId, object_id: str) -> list[Edge]: ...

    async def append_event(self, tenant_id: TenantId, event: Event) -> None: ...

    async def list_events(
        self,
        tenant_id: TenantId,
        *,
        object_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 200,
    ) -> list[Event]:
        """Newest first. Returned events are copies: the log is append-only, and a
        caller mutating what it was handed must not be able to rewrite history."""
        ...

    async def search(
        self,
        tenant_id: TenantId,
        query: str,
        *,
        scope: Scope | None = None,
        caller_surface: Provider | None = None,
        include_restricted: bool = False,
        top_k: int = 12,
    ) -> list[Scored]:
        """Hybrid vector + lexical retrieval over one tenant's active objects, scope
        and locality per the module docstring. Returns `Scored` descending.

        Backends narrow candidates however they can -- the in-process store keeps a
        vector index per tenant, Postgres filters on `tenant_id` before the ANN scan
        -- but all of them blend through `rank_score`, so a backend swap changes
        performance and not which memory a model sees (§5.1).
        """
        ...
