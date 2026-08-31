"""Postgres + pgvector backend (SCOPE §5).

The table shape is `migrations/001_init.sql`; this is the psycopg wiring over it.
Three invariants this module exists to hold, all of them from AGENTS.md rather than
from taste:

  * **One transaction per write.** The object row, its embedding and its event row
    commit together. The event log is the provenance record -- a row that exists
    without its event is a data-integrity failure we cannot detect later, so it must
    not be possible to produce one, not merely discouraged.
  * **No DELETE, ever.** `retire_object` sets `retired_at`. Nothing in this package
    issues DELETE or UPDATE against `event_log` at all.
  * **Identical ranking to the in-process store.** Postgres narrows the candidate
    set (cosine top-k unioned with a trigram match, which is the part a database is
    genuinely better at); the final blend runs through
    `coletar.retrieval.ranking.rank_score`, the same call `InMemoryStore` makes. A
    backend swap must not change which memory a model sees.
  * **Tenant on every statement.** Identity is `(tenant_id, id)`, enforced by the
    primary keys and by tenant-aware foreign keys from migration 002 -- so a
    cross-tenant edge or `supersedes` is refused by the database even if application
    code asks for one. Every read below filters, including `get_object` and
    `list_events`; the latter is the worst leak available, since event rows carry
    full before/after object state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
from pgvector.psycopg import register_vector_async
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from coletar.retrieval.embedding import Embedder, build_embedder, cosine, tokenize
from coletar.retrieval.ranking import Scored, lexical_score, rank_score
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    ContextObject,
    Edge,
    ObjectType,
    Provider,
    Scope,
    ScopeType,
    object_from_record,
)
from coletar.schema.tenancy import CrossTenantError, TenantId

#: Always qualified, and every query below aliases `context_object` as `o`. The
#: search query joins against a candidate CTE that also has an `id`, so an
#: unqualified list is ambiguous there and nowhere else -- which is exactly the kind
#: of difference that is better removed than remembered.
_OBJECT_COLUMNS = """
    o.id, o.type, o.content, o.scope_type, o.scope_id, o.locality_mode,
    o.locality_surfaces, o.kind, o.confidence,
    o.extraction_method, o.sensitivity, o.supersedes, o.provenance,
    o.provider_mappings, o.payload, o.version, o.created_at, o.updated_at,
    o.retired_at, o.ttl_days
"""


#: Appended wherever a query already filters on tenant/active/scope, exactly the way
#: `scope_clause` is built below -- present only when a caller names a surface, since
#: `caller_surface=None` (a trusted internal caller) applies no restriction at all.
def _locality_clause(caller_surface: Provider | None) -> tuple[str, list[Any]]:
    if caller_surface is None:
        return "", []
    return "AND (o.locality_mode = 'synced' OR o.locality_surfaces ? %s)", [str(caller_surface)]


#: An object is active when nothing retired it and nothing supersedes it. The second
#: half matters between a correction being written and compression next running --
#: retrieval must not serve the stale fact in that window (§6). The supersession
#: subquery is itself tenant-scoped: another tenant's correction is none of ours.
#: Bounds the supersedes walk. A cycle is bad data, not a reason to hang.
_MAX_SUPERSEDES_DEPTH = 16

_ACTIVE_PREDICATE = """
    o.tenant_id = %s
    AND o.retired_at IS NULL
    AND NOT EXISTS (
        SELECT 1 FROM context_object s
        WHERE s.tenant_id = o.tenant_id AND s.supersedes = o.id
    )
"""


def _to_record(row: dict[str, Any]) -> ContextObject:
    record: dict[str, Any] = {
        "id": row["id"],
        "type": row["type"],
        "content": row["content"],
        "scope": {"type": row["scope_type"], "id": row["scope_id"]},
        "locality": {"mode": row["locality_mode"], "surfaces": row["locality_surfaces"]},
        "confidence": row["confidence"],
        "extraction_method": row["extraction_method"],
        "sensitivity": row["sensitivity"],
        "supersedes": row["supersedes"],
        "provenance": row["provenance"],
        "provider_mappings": row["provider_mappings"],
        "payload": row["payload"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "retired_at": row["retired_at"],
        "ttl_days": row["ttl_days"],
    }
    if row.get("kind") is not None:
        record["kind"] = row["kind"]
    return object_from_record(record)


class PostgresStore:
    def __init__(
        self,
        dsn: str,
        *,
        embedding_dim: int = 768,
        embedder: Embedder | None = None,
    ) -> None:
        self.dsn = dsn
        self.embedding_dim = embedding_dim
        self._embedder = embedder or build_embedder()
        self._pool: AsyncConnectionPool | None = None

    @property
    def embedder_model(self) -> str:
        return self._embedder.model

    async def _get_pool(self) -> AsyncConnectionPool:
        if self._pool is None:
            # `configure` runs on every pooled connection, registering pgvector's
            # type adapters so embeddings cross the wire in binary as `vector`
            # rather than as a hand-formatted decimal string. It reads the type OID
            # from the database, so the `vector` extension must already exist --
            # run `coletar migrate` before pointing a store at a fresh database.
            pool = AsyncConnectionPool(self.dsn, open=False, configure=register_vector_async)
            await pool.open(wait=True)
            self._pool = pool
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # -- objects ------------------------------------------------------------
    async def put_object(
        self, tenant_id: TenantId, obj: ContextObject, *, event: Event | None = None
    ) -> ContextObject:
        vector = (await self._embedder.embed([obj.content]))[0]
        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_OBJECT_COLUMNS} FROM context_object o "
                f"WHERE o.tenant_id = %s AND o.id = %s",
                (tenant_id, obj.id),
            )
            existing_row = await cur.fetchone()
            before = _to_record(existing_row).model_dump(mode="json") if existing_row else None
            # Detached copy, for the same reason InMemoryStore keeps one: put_object
            # does not mutate what it was handed.
            obj = obj.model_copy(deep=True)
            if existing_row is not None:
                obj.touch()

            if obj.supersedes is not None:
                # Checked here as well as by the composite foreign key, so both
                # backends fail the same way with the same message.
                await cur.execute(
                    "SELECT 1 FROM context_object WHERE tenant_id = %s AND id = %s",
                    (tenant_id, obj.supersedes),
                )
                if await cur.fetchone() is None:
                    raise CrossTenantError(
                        f"supersedes {obj.supersedes!r} is not an object in "
                        f"tenant {tenant_id!r}"
                    )

            dump = obj.model_dump(mode="json")
            await cur.execute(
                """
                INSERT INTO context_object (
                    tenant_id, id, type, content, scope_type, scope_id,
                    locality_mode, locality_surfaces, kind, confidence,
                    extraction_method, sensitivity, supersedes, provenance,
                    provider_mappings, payload, version, created_at, updated_at,
                    retired_at, ttl_days
                ) VALUES (
                    %(tenant_id)s, %(id)s, %(type)s, %(content)s, %(scope_type)s,
                    %(scope_id)s, %(locality_mode)s, %(locality_surfaces)s, %(kind)s,
                    %(confidence)s, %(extraction_method)s, %(sensitivity)s, %(supersedes)s,
                    %(provenance)s, %(provider_mappings)s, %(payload)s, %(version)s,
                    %(created_at)s, %(updated_at)s, %(retired_at)s, %(ttl_days)s
                )
                ON CONFLICT (tenant_id, id) DO UPDATE SET
                    content = EXCLUDED.content,
                    scope_type = EXCLUDED.scope_type,
                    scope_id = EXCLUDED.scope_id,
                    locality_mode = EXCLUDED.locality_mode,
                    locality_surfaces = EXCLUDED.locality_surfaces,
                    kind = EXCLUDED.kind,
                    confidence = EXCLUDED.confidence,
                    extraction_method = EXCLUDED.extraction_method,
                    sensitivity = EXCLUDED.sensitivity,
                    supersedes = EXCLUDED.supersedes,
                    provenance = EXCLUDED.provenance,
                    provider_mappings = EXCLUDED.provider_mappings,
                    payload = EXCLUDED.payload,
                    version = EXCLUDED.version,
                    updated_at = EXCLUDED.updated_at,
                    retired_at = EXCLUDED.retired_at,
                    ttl_days = EXCLUDED.ttl_days
                """,
                _object_params(tenant_id, obj, dump),
            )
            await cur.execute(
                """
                INSERT INTO object_embedding (tenant_id, object_id, model, embedding)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (tenant_id, object_id) DO UPDATE SET
                    model = EXCLUDED.model,
                    embedding = EXCLUDED.embedding,
                    created_at = now()
                """,
                (tenant_id, obj.id, self._embedder.model, np.asarray(vector, dtype=np.float32)),
            )

            base = event or Event(
                type=EventType.OBJECT_UPDATED if existing_row else EventType.OBJECT_CREATED,
                object_id=obj.id,
                actor=Actor.SYSTEM,
                provider=obj.provenance.provider,
                detail={"type": obj.type, "scope": str(obj.scope)},
            )
            await _insert_event(
                cur, tenant_id, base.model_copy(update={"before": before, "after": dump})
            )
            # The `async with` commits: object, embedding and event land together.
        return obj

    async def get_object(
        self, tenant_id: TenantId, object_id: str, *, caller_surface: Provider | None = None
    ) -> ContextObject | None:
        locality_clause, locality_params = _locality_clause(caller_surface)
        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_OBJECT_COLUMNS} FROM context_object o "
                f"WHERE o.tenant_id = %s AND o.id = %s {locality_clause}",
                (tenant_id, object_id, *locality_params),
            )
            row = await cur.fetchone()
        return _to_record(row) if row else None

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
        clauses: list[str] = ["o.tenant_id = %s"]
        params: list[Any] = [tenant_id]
        if type is not None:
            clauses.append("o.type = %s")
            params.append(str(type))
        if scope is not None:
            clauses.append("o.scope_type = %s AND o.scope_id IS NOT DISTINCT FROM %s")
            params.extend([str(scope.type), scope.id])
        locality_clause, locality_params = _locality_clause(caller_surface)
        if locality_clause:
            clauses.append(locality_clause.removeprefix("AND "))
            params.extend(locality_params)
        if not include_retired:
            clauses.append("o.retired_at IS NULL")
        if not include_superseded:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM context_object s "
                "WHERE s.tenant_id = o.tenant_id AND s.supersedes = o.id)"
            )
        # Unconditional: the tenant predicate is always present, and a form that
        # can produce an empty WHERE is one refactor away from a cross-tenant scan.
        where = f"WHERE {' AND '.join(clauses)}"

        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_OBJECT_COLUMNS} FROM context_object o {where} "
                f"ORDER BY o.updated_at DESC LIMIT %s",
                (*params, limit),
            )
            rows = await cur.fetchall()
        return [_to_record(row) for row in rows]

    async def retire_object(self, tenant_id: TenantId, object_id: str, *, reason: str) -> None:
        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_OBJECT_COLUMNS} FROM context_object o "
                f"WHERE o.tenant_id = %s AND o.id = %s AND o.retired_at IS NULL FOR UPDATE",
                (tenant_id, object_id),
            )
            row = await cur.fetchone()
            if row is None:
                return
            before = _to_record(row).model_dump(mode="json")
            # Soft retire. There is no code path in this package that DELETEs.
            await cur.execute(
                f"UPDATE context_object AS o SET retired_at = now() "
                f"WHERE o.tenant_id = %s AND o.id = %s RETURNING {_OBJECT_COLUMNS}",
                (tenant_id, object_id),
            )
            after_row = await cur.fetchone()
            assert after_row is not None
            await _insert_event(
                cur,
                tenant_id,
                Event(
                    type=EventType.OBJECT_RETIRED,
                    object_id=object_id,
                    actor=Actor.JOB,
                    before=before,
                    after=_to_record(after_row).model_dump(mode="json"),
                    detail={"reason": reason},
                ),
            )

    # -- edges --------------------------------------------------------------
    async def add_edge(self, tenant_id: TenantId, edge: Edge) -> None:
        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            for endpoint in (edge.src_id, edge.dst_id):
                await cur.execute(
                    "SELECT 1 FROM context_object WHERE tenant_id = %s AND id = %s",
                    (tenant_id, endpoint),
                )
                if await cur.fetchone() is None:
                    raise CrossTenantError(
                        f"edge endpoint {endpoint!r} is not in tenant {tenant_id!r}"
                    )
            # The primary key is (tenant_id, src_id, dst_id, type), so this is
            # idempotent in the schema rather than in a check the caller could forget.
            await cur.execute(
                """
                INSERT INTO context_edge (tenant_id, src_id, dst_id, type, confidence, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, src_id, dst_id, type) DO NOTHING
                """,
                (tenant_id, edge.src_id, edge.dst_id, str(edge.type), edge.confidence,
                 edge.created_at),
            )
            if cur.rowcount == 0:
                return  # already asserted; no second row and no second event
            await _insert_event(
                cur,
                tenant_id,
                Event(
                    type=EventType.EDGE_CREATED,
                    object_id=edge.src_id,
                    detail={"dst": edge.dst_id, "edge_type": edge.type},
                ),
            )

    async def edges_from(self, tenant_id: TenantId, object_id: str) -> list[Edge]:
        return await self._edges(tenant_id, "src_id", object_id)

    async def edges_to(self, tenant_id: TenantId, object_id: str) -> list[Edge]:
        return await self._edges(tenant_id, "dst_id", object_id)

    async def _edges(self, tenant_id: TenantId, column: str, object_id: str) -> list[Edge]:
        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT src_id, dst_id, type, confidence, created_at "
                f"FROM context_edge WHERE tenant_id = %s AND {column} = %s",
                (tenant_id, object_id),
            )
            rows = await cur.fetchall()
        return [Edge.model_validate(row) for row in rows]

    # -- event log ----------------------------------------------------------
    async def append_event(self, tenant_id: TenantId, event: Event) -> None:
        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await _insert_event(cur, tenant_id, event)

    async def list_events(
        self,
        tenant_id: TenantId,
        *,
        object_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 200,
    ) -> list[Event]:
        clauses: list[str] = ["tenant_id = %s"]
        params: list[Any] = [tenant_id]
        if object_id is not None:
            clauses.append("object_id = %s")
            params.append(object_id)
        if since is not None:
            clauses.append("at >= %s")
            params.append(since)
        if until is not None:
            clauses.append("at <= %s")
            params.append(until)
        # Unconditional: the tenant predicate is always present, and a form that
        # can produce an empty WHERE is one refactor away from a cross-tenant scan.
        where = f"WHERE {' AND '.join(clauses)}"

        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT id, type, object_id, actor, provider, at, detail "
                f"FROM event_log {where} ORDER BY at DESC, id DESC LIMIT %s",
                (*params, limit),
            )
            rows = await cur.fetchall()
        return [_event_from_row(row) for row in rows]

    # -- retrieval ----------------------------------------------------------
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
        query_vector = (await self._embedder.embed([query]))[0]
        query_array = np.asarray(query_vector, dtype=np.float32)

        scope_clause = ""
        scope_params: list[Any] = []
        if scope is not None:
            # A conversation inside a project still sees global context; it never
            # sees another project's. See the Store protocol docstring.
            if scope.type is ScopeType.PROJECT:
                scope_clause = "AND (o.scope_type = 'global' OR o.scope_id = %s)"
                scope_params.append(scope.id)
            else:
                scope_clause = "AND o.scope_type = 'global'"
        locality_clause, locality_params = _locality_clause(caller_surface)
        sensitivity_clause = "" if include_restricted else "AND o.sensitivity <> 'restricted'"

        # Over-fetch from each half: the final ordering is the blend below, so the
        # candidate pool has to be wider than the number of rows we return.
        fetch = max(top_k * 4, 50)
        # Candidate generation deliberately does *not* exclude superseded rows, and
        # deliberately does not apply scope, locality or sensitivity. A correction
        # rarely repeats the value it corrects, so the only text matching "is Chris
        # still at Acme?" is the sentence being retired -- narrowing it away here is
        # what no reranker could repair. `resolve` walks each candidate forward to the
        # object that now speaks for it, and every policy filter is applied to *that*
        # row in the outer WHERE, never to the ancestor that merely matched.
        sql = f"""
        WITH RECURSIVE cand AS (
            (
                SELECT o.id FROM context_object o
                JOIN object_embedding e
                  ON e.tenant_id = o.tenant_id AND e.object_id = o.id
                WHERE o.tenant_id = %s AND o.retired_at IS NULL
                ORDER BY e.embedding <=> %s
                LIMIT %s
            )
            UNION
            (
                SELECT o.id FROM context_object o
                WHERE o.tenant_id = %s AND o.retired_at IS NULL
                  AND o.content %% %s
                LIMIT %s
            )
        ),
        resolve(cand_id, cur_id, depth) AS (
            SELECT c.id, c.id, 0 FROM cand c
            UNION ALL
            SELECT r.cand_id, s.id, r.depth + 1
            FROM resolve r
            JOIN context_object s
              ON s.tenant_id = %s AND s.supersedes = r.cur_id
            -- A correction cycle is bad data, not a reason to hang the query.
            WHERE r.depth < {_MAX_SUPERSEDES_DEPTH}
        ),
        head AS (
            SELECT DISTINCT ON (cand_id) cand_id, cur_id
            FROM resolve ORDER BY cand_id, depth DESC
        )
        SELECT {_OBJECT_COLUMNS},
               e.embedding AS embedding,
               m.content AS match_content,
               me.embedding AS match_embedding
        FROM head h
        JOIN context_object o ON o.tenant_id = %s AND o.id = h.cur_id
        JOIN context_object m ON m.tenant_id = %s AND m.id = h.cand_id
        LEFT JOIN object_embedding e
          ON e.tenant_id = o.tenant_id AND e.object_id = o.id
        LEFT JOIN object_embedding me
          ON me.tenant_id = m.tenant_id AND me.object_id = m.id
        WHERE o.retired_at IS NULL
          {scope_clause} {locality_clause} {sensitivity_clause}
        """
        params = [
            tenant_id, query_array, fetch,
            tenant_id, query, fetch,
            tenant_id,
            tenant_id,
            tenant_id,
            *scope_params, *locality_params,
        ]

        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

        query_tokens = set(tokenize(query))
        # Keyed by the object actually returned: a correction reached through two
        # different stale ancestors is one hit at its best score, not two.
        best: dict[str, Scored] = {}
        for row in rows:
            obj = _to_record(row)
            # pgvector hands back its own `Vector` wrapper; `to_list()` is its
            # accessor, and the ranking blend below wants plain floats.
            embedding = row.get("match_embedding")
            vector = [] if embedding is None else embedding.to_list()
            # Relevance comes from the text that matched; trust and recency come from
            # the object being returned. Scoring a correction by its ancestor's
            # confidence would let a retired guess vouch for what replaced it.
            lexical = lexical_score(query_tokens, set(tokenize(str(row["match_content"]))))
            similarity = cosine(query_vector, vector)
            if lexical <= 0.0 and similarity <= 0.0:
                continue
            hit = Scored(
                obj=obj,
                components=rank_score(
                    lexical=lexical,
                    vector=similarity,
                    confidence=obj.confidence,
                    updated_at=obj.updated_at,
                ),
            )
            current = best.get(obj.id)
            if current is None or hit.score > current.score:
                best[obj.id] = hit

        scored = sorted(best.values(), key=lambda hit: (hit.score, hit.obj.id), reverse=True)
        return scored[:top_k]


def _object_params(
    tenant_id: TenantId, obj: ContextObject, dump: dict[str, Any]
) -> dict[str, Any]:
    from psycopg.types.json import Jsonb

    return {
        "tenant_id": tenant_id,
        "id": obj.id,
        "type": str(obj.type),
        "content": obj.content,
        "scope_type": str(obj.scope.type),
        "scope_id": obj.scope.id,
        "locality_mode": str(obj.locality.mode),
        "locality_surfaces": Jsonb(sorted(str(s) for s in obj.locality.surfaces)),
        "kind": str(kind) if (kind := getattr(obj, "kind", None)) is not None else None,
        "confidence": obj.confidence,
        "extraction_method": str(obj.extraction_method),
        "sensitivity": str(obj.sensitivity),
        "supersedes": obj.supersedes,
        "provenance": Jsonb(dump["provenance"]),
        "provider_mappings": Jsonb(dump["provider_mappings"]),
        "payload": Jsonb(dump["payload"]),
        "version": obj.version,
        "created_at": obj.created_at,
        "updated_at": obj.updated_at,
        "retired_at": obj.retired_at,
        "ttl_days": obj.ttl_days,
    }


async def _insert_event(cur: Any, tenant_id: TenantId, event: Event) -> None:
    """The only INSERT into `event_log`, and there is no UPDATE or DELETE anywhere.

    before/after ride inside `detail` so the log stays a single append-only table
    with a stable shape; `_event_from_row` lifts them back out.
    """
    from psycopg.types.json import Jsonb

    detail = dict(event.detail)
    if event.before is not None:
        detail["__before"] = event.before
    if event.after is not None:
        detail["__after"] = event.after
    await cur.execute(
        """
        INSERT INTO event_log (tenant_id, id, type, object_id, actor, provider, at, detail)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            tenant_id,
            event.id,
            str(event.type),
            event.object_id,
            str(event.actor),
            str(event.provider),
            event.at,
            Jsonb(detail),
        ),
    )


def _event_from_row(row: dict[str, Any]) -> Event:
    detail = dict(row["detail"] or {})
    before = detail.pop("__before", None)
    after = detail.pop("__after", None)
    return Event(
        id=row["id"],
        type=row["type"],
        object_id=row["object_id"],
        actor=row["actor"],
        provider=row["provider"],
        at=row["at"],
        before=before,
        after=after,
        detail=detail,
    )

