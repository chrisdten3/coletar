"""Interactive local product prototype over the existing Store and compiler.

The browser never receives provider credentials. Setup/key simulations live only in
browser preferences; graph operations below always use the tenant's canonical store.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from coletar.accounts.models import LOCAL_IDENTITY
from coletar.acquisition import chatgpt_export, claude_export
from coletar.capture import capture_turn, is_pending
from coletar.compiler import ChatGPTCompiler, ClaudeCompiler, LocalModelCompiler
from coletar.compiler.base import Compiler
from coletar.compiler.continuity import WEIGHTS
from coletar.config import get_settings
from coletar.episode_crypto import EpisodeKeyUnavailable, decrypt_episode
from coletar.extraction import extract_memories
from coletar.history.clusters import cluster_mass, movers
from coletar.history.nl import EXAMPLE_QUESTIONS, compile_question
from coletar.history.query import (
    MAX_WINDOW_DAYS,
    Bucket,
    GroupBy,
    HistoryQuery,
    Metric,
    QueryResult,
)
from coletar.history.rollup import object_lifetime, reach_report, run_query
from coletar.history.threads import DEFAULT_MAX_OBJECTS, run_thread, suggest_threads
from coletar.ingest import remember
from coletar.inspector.auth import Tenant, local_mode
from coletar.inspector.review import edit, mark_reviewed, review_status
from coletar.pricing import BY_MODEL, SETTING_KEY, resolve
from coletar.retrieval.trace import ComponentVersions, RetrievalTrace, query_digest
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    GLOBAL_SCOPE,
    ContextObject,
    Edge,
    EdgeType,
    ExtractionMethod,
    Locality,
    LocalityMode,
    Memory,
    MemoryKind,
    ObjectType,
    OriginType,
    Provider,
    Scope,
    ScopeType,
)
from coletar.schema.tenancy import TenantId
from coletar.store import build_store
from coletar.store.base import Store
from coletar.temporal import graph_as_of


async def same_origin(request: Request) -> None:
    if request.method == "POST":
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Use this app from its local workspace origin.")


router = APIRouter(dependencies=[Depends(same_origin)])
Destination = Literal["chatgpt", "claude", "local", "markdown"]


#: Deliberately absent: a module-level `tenant()` reading
#: `COLETAR_DEFAULT_TENANT_ID`. Every route below takes `owner: Tenant` instead, so
#: the graph a request reaches is decided by who signed in and by nothing else. See
#: `coletar.inspector.auth` for why, and for how local development keeps working
#: without a sign-in.


class MemoryInput(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    kind: MemoryKind = MemoryKind.FACT
    project: str = Field(default="", max_length=200)
    locality: Locality = Field(default_factory=Locality)


class ActionInput(BaseModel):
    action: Literal["review", "retire", "edit", "reach"]
    content: str = Field(default="", max_length=20000)
    locality: Locality = Field(default_factory=Locality)


class ReviewInput(BaseModel):
    ids: list[str] = Field(max_length=1000)


class CompileInput(BaseModel):
    destination: Destination


class ImportResult(BaseModel):
    conversations: int
    turns: int
    memories: int
    corroborated: int
    extractor: str = "Local pattern extractor (no model calls)"


class Snapshot(BaseModel):
    hosted: bool = False
    tenant: str
    objects: list[dict[str, Any]]
    unreviewed: list[str]
    events: list[dict[str, Any]]
    can_compile: bool
    usage: dict[str, int]
    sample: bool
    conflicts: list[tuple[str, str]]


async def load_object(owner: TenantId, object_id: str) -> ContextObject:
    obj = await build_store().get_object(owner, object_id)
    if obj is None:
        raise HTTPException(404, "Object not found in this workspace")
    return obj


async def safe_object(store: Store, owner: TenantId, obj: ContextObject) -> dict[str, Any]:
    result = obj.model_dump(mode="json")
    if obj.type is ObjectType.EPISODE:
        try:
            result["content"] = await decrypt_episode(store, owner, obj)
        except EpisodeKeyUnavailable:
            result["content"] = "This captured turn has been erased."
        result["pending"] = is_pending(obj)
    return result


_STATIC = Path(__file__).parent / "static"


#: Files whose contents decide the cache-busting stamp.
_ASSETS = (
    "product.css",
    "product.js",
    "icons.svg",
    "horizon.css",
    "horizon.js",
    # Served with the same cache stamp as the rest. Omitting it would mean a change
    # to how sign-in works reaches nobody with the page already cached — which is
    # the one asset where a stale copy is a lockout rather than a cosmetic bug.
    "auth.js",
)


def _asset_signature() -> tuple[tuple[str, int, int], ...]:
    """A cheap stand-in for hashing every asset on every request."""
    out = []
    for name in _ASSETS:
        stat = (_STATIC / name).stat()
        out.append((name, stat.st_mtime_ns, stat.st_size))
    return tuple(out)


def _sign_in_config(request: Request) -> dict[str, Any]:
    """What the shell needs to know before it can decide whether to ask for a login.

    Stamped into the HTML rather than fetched, because the alternative is a round
    trip during which the app does not know whether it is allowed to render — which
    is a flash of the workspace for someone who is about to be told to sign in.

    **Every value here is publishable, and that is a property worth keeping.** A
    Clerk publishable key and a Supabase anon key are both meant to reach a browser;
    coleta reads neither provider's secret anywhere, because verification is a
    signature check against a public JWKS. If a future provider needs a secret in
    this dict, that is the signal that it does not fit the seam.
    """
    settings = get_settings()
    provider = settings.identity_provider
    return {
        "provider": provider if provider else LOCAL_IDENTITY,
        # Clerk. Kept alongside Supabase rather than replaced: the provider is one
        # setting, and a deployment must be able to move back without a redeploy of
        # the client.
        "publishableKey": settings.clerk_publishable_key,
        # Supabase Auth.
        "supabaseUrl": settings.supabase_url,
        "supabaseAnonKey": settings.supabase_anon_key,
        # What the client branches on, and now the same condition the server
        # enforces rather than a second opinion about it. It used to read
        # `provider not in {"", LOCAL_IDENTITY}` while the server asked whether
        # `public_url` was set, so the two could disagree: a deployment could
        # hide its sign-in link while still gating the API, or vice versa.
        # `local_mode` is loopback-only, so a deployed host always gets a
        # sign-in and no environment variable can turn that off.
        "required": not local_mode(request),
        # Whether a stranger may create their own workspace. The client uses this to
        # decide whether to offer a sign-up route at all, so that an invite-only
        # deployment does not advertise a door that answers "not yet".
        "openRegistration": settings.open_registration,
    }


@lru_cache(maxsize=4)
def _render_app_html(signature: tuple[tuple[str, int, int], ...], config: str) -> str:
    digest = sha256()
    for name in _ASSETS:
        digest.update((_STATIC / name).read_bytes())
    return (
        (_STATIC / "product.html")
        .read_text()
        .replace("__ASSETS__", digest.hexdigest()[:12])
        .replace("__SIGN_IN__", config)
    )


def _app_html(request: Request) -> str:
    """The shell, stamped with a digest of its own assets.

    Browsers cache /static aggressively, and a deploy that changes the client
    without changing its URL is a deploy that reaches nobody.

    Keyed on the assets' mtime and size rather than cached outright. Caching it
    outright is correct in production, where a deploy restarts the process — but
    locally the process outlives the edit, so the stamp froze at whatever the files
    said on the first request and every later change was served under a URL the
    browser already had. The symptom is the worst kind: the server *is* serving new
    code, and the browser shows old.
    """
    # The config is part of the cache key: a deployment that switches identity
    # provider without restarting must not keep serving the old shell.
    return _render_app_html(_asset_signature(), json.dumps(_sign_in_config(request)))


@router.get("/app", include_in_schema=False)
async def product_app(request: Request) -> HTMLResponse:
    return HTMLResponse(_app_html(request))


@router.get("/web-api/state", response_model=Snapshot)
async def state(owner: Tenant) -> Snapshot:
    store = build_store()
    status = await review_status(store, owner)
    objects = await store.list_objects(
        owner, include_retired=True, include_superseded=True, limit=10000
    )
    events = await store.list_events(owner, limit=2000)
    usage: dict[str, int] = {}
    for event in events:
        if event.type is EventType.RETRIEVAL_TRACE:
            # Group by which assistant asked, falling back to the door it came
            # through for traces written before `provider` was recorded. "How much
            # context has Claude been served" is the question; every Claude and
            # ChatGPT surface shares the one `mcp` door, so grouping by door
            # cannot answer it.
            who = str(event.detail.get("provider") or event.detail.get("surface", "unknown"))
            usage[who] = usage.get(who, 0) + int(event.detail.get("token_estimate", 0))
    conflicts = []
    edges_by_src = await store.edges_from_many(owner, [o.id for o in objects])
    for edge_list in edges_by_src.values():
        for edge in edge_list:
            if edge.type is EdgeType.CONTRADICTS:
                conflicts.append((edge.src_id, edge.dst_id))
    return Snapshot(
        hosted=bool(get_settings().public_url),
        conflicts=conflicts,
        tenant=str(owner),
        objects=[await safe_object(store, owner, o) for o in objects],
        unreviewed=[o.id for o in status.unreviewed],
        can_compile=status.can_compile,
        # Events can contain encrypted raw turns, but never their content keys.
        events=[e.model_dump(mode="json") for e in events],
        usage=usage,
        sample=any(o.payload.get("design_sample") for o in objects),
    )


@router.post("/web-api/memories")
async def add_memory(body: MemoryInput, owner: Tenant) -> dict[str, str]:
    if not body.content.strip():
        raise HTTPException(422, "Write a memory before saving.")
    obj = Memory.from_write(
        body.content.strip(),
        kind=body.kind,
        provider=Provider.COLETAR,
        extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
        origin_type=OriginType.USER,
        scope=Scope(type=ScopeType.PROJECT, id=body.project.strip())
        if body.project.strip()
        else GLOBAL_SCOPE,
        locality=body.locality,
    )
    result = await remember(
        build_store(),
        owner,
        obj,
        event=Event(
            type=EventType.OBJECT_CREATED,
            actor=Actor.USER,
            object_id=obj.id,
            detail={"surface": "web-app"},
        ),
    )
    return {"id": result.object_id}


@router.post("/web-api/objects/{object_id}")
async def object_action(object_id: str, body: ActionInput, owner: Tenant) -> dict[str, str]:
    obj = await load_object(owner, object_id)
    if obj.type is ObjectType.EPISODE:
        raise HTTPException(422, "Raw captured turns are source evidence, not editable memories.")
    if obj.retired_at is not None:
        raise HTTPException(409, "This object is retired. Its history remains readable.")
    store = build_store()
    if body.action == "review":
        await mark_reviewed(store, owner, object_id)
    elif body.action == "retire":
        await store.retire_object(owner, object_id, reason="Retired by user in web app")
    elif body.action == "edit":
        if not body.content.strip():
            raise HTTPException(422, "Content cannot be empty. Retire the object instead.")
        await edit(store, owner, object_id, content=body.content)
    else:
        if body.locality != obj.locality:
            before = str(obj.locality)
            obj.locality = body.locality
            await store.put_object(
                owner,
                obj,
                event=Event(
                    type=EventType.OBJECT_UPDATED,
                    actor=Actor.USER,
                    object_id=obj.id,
                    detail={"field": "locality", "from": before, "to": str(body.locality)},
                ),
            )
            # Changing who may see a fact does not constitute reviewing its content.
    return {"id": object_id}


@router.post("/web-api/review")
async def review_many(body: ReviewInput, owner: Tenant) -> dict[str, int]:
    status = await review_status(build_store(), owner)
    eligible = {o.id for o in status.unreviewed}
    if not set(body.ids).issubset(eligible):
        raise HTTPException(409, "The review queue changed. Refresh it and try again.")
    for object_id in set(body.ids):
        await mark_reviewed(build_store(), owner, object_id)
    return {"reviewed": len(set(body.ids))}


@router.get("/web-api/objects/{object_id}/reads")
async def object_reads(object_id: str, owner: Tenant) -> dict[str, Any]:
    """Which surfaces have been served this object.

    The counterpart to the object's lineage, which answers who wrote it. Loading
    the object first is not redundant: it is what keeps the tenant filter on this
    path identical to every other one.
    """
    obj = await load_object(owner, object_id)
    receipts = await build_store().reads_of(owner, obj.id, limit=50)
    return {
        "object_id": obj.id,
        "restricted": obj.locality.mode is LocalityMode.LOCAL_ONLY,
        "reads": [r.model_dump(mode="json") for r in receipts],
    }


class GraphNode(BaseModel):
    id: str
    label: str
    #: An entity's one-line identity, shown on hover. Empty for facts, which say
    #: everything they have to say in the label.
    description: str = ""
    type: str
    #: How many facts mention this entity. The Atlas view sizes and orders by it,
    #: because a hub with nine facts is the thing worth drawing and a name
    #: mentioned once is the long tail.
    degree: int = 0


class GraphEdge(BaseModel):
    src: str
    dst: str


class ContextGraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    #: Entities that exist but are not drawn, so the view can say so rather than
    #: quietly implying the graph is smaller than it is.
    omitted_entities: int = 0
    total_entities: int = 0


@router.get("/web-api/graph", response_model=ContextGraph)
async def context_graph(owner: Tenant, limit: int = 60, focus: str | None = None) -> ContextGraph:
    """Entities and the facts that mention them, small enough to draw.

    A separate endpoint rather than more fields on `/web-api/state`: the snapshot
    already ships every object to the browser, and the graph needs the *opposite*
    shape — labels and edges, no payloads, no provenance. Sending both would be
    sending the same corpus twice in two formats.

    Unfocused, this returns the most-mentioned entities, because 1,722 nodes is not
    a picture. `focus` returns one entity and everything that mentions it, which is
    the drill-down the hub-and-spoke layout is for.
    """
    store = build_store()
    objects = await store.list_objects(owner, limit=10000)
    by_id = {o.id: o for o in objects}

    # Facts point at entities, so the mention count per entity is a reverse lookup.
    mentions: list[tuple[str, str]] = []
    facts = [o.id for o in objects if o.type is ObjectType.FACT]
    edges_by_src = await store.edges_from_many(owner, facts)
    for fact_id in facts:
        for edge in edges_by_src.get(fact_id, ()):
            if edge.type is EdgeType.MENTIONS and edge.dst_id in by_id:
                mentions.append((fact_id, edge.dst_id))

    degree: dict[str, int] = {}
    for _, entity_id in mentions:
        degree[entity_id] = degree.get(entity_id, 0) + 1

    entities = [o for o in objects if o.type is ObjectType.ENTITY]
    if focus is not None:
        if focus not in by_id:
            raise HTTPException(404, "No such object in this workspace.")
        keep = {focus} | {f for f, e in mentions if e == focus} | {
            e for f, e in mentions if f == focus
        }
    else:
        ranked = sorted(entities, key=lambda o: (-degree.get(o.id, 0), o.content))
        # An entity nothing mentions is an island; drawing hundreds of them is what
        # makes a graph view look like static.
        hubs = [o.id for o in ranked if degree.get(o.id, 0) > 0][:limit]
        keep = set(hubs) | {f for f, e in mentions if e in set(hubs)}

    nodes = [
        GraphNode(
            id=o.id,
            # An entity's identity is its name; `content` holds the one-line
            # description, which reads as "An investment bank." on a node that
            # should say "JPMorgan". Facts have no name and are their own label.
            label=str(o.payload.get("name") or o.content),
            description=o.content if o.payload.get("name") else "",
            type=o.type.value,
            degree=degree.get(o.id, 0),
        )
        for oid in keep
        if (o := by_id.get(oid)) is not None
    ]
    drawn = {n.id for n in nodes}
    if focus is None:
        # Entities do not link to each other; facts link to entities. So the
        # overview's edges are co-mentions — two entities joined because one fact
        # names both. That is a real relation in the data ("a fact about my time
        # at Georgetown that also names Hoya Developers"), and without it the
        # constellation is a scatter of circles rather than a graph.
        per_fact: dict[str, list[str]] = {}
        for fact_id, entity_id in mentions:
            if entity_id in drawn:
                per_fact.setdefault(fact_id, []).append(entity_id)
        pairs: set[tuple[str, str]] = set()
        for named in per_fact.values():
            for i, a in enumerate(named):
                for b in named[i + 1 :]:
                    if a != b:
                        pairs.add((a, b) if a < b else (b, a))
        graph_edges = [GraphEdge(src=a, dst=b) for a, b in sorted(pairs)]
        nodes = [n for n in nodes if n.type == "entity"]
    else:
        graph_edges = [
            GraphEdge(src=f, dst=e) for f, e in mentions if f in drawn and e in drawn
        ]

    linked = len([o for o in entities if degree.get(o.id, 0) > 0])
    return ContextGraph(
        nodes=nodes,
        edges=graph_edges,
        total_entities=len(entities),
        omitted_entities=max(0, linked - sum(1 for n in nodes if n.type == "entity")),
    )


class LibraryGroup(BaseModel):
    """One section of the Library, and what to call it."""

    key: str
    label: str
    #: "entity" | "project" | "loose". The client styles them differently and a
    #: person reads them differently: an entity is a thing the facts are *about*,
    #: a project is a scope they were filed under.
    kind: str
    description: str = ""
    #: Ids only. The browser already holds every object from `/web-api/state`, and
    #: sending them again in a second shape would be sending the same corpus twice.
    object_ids: list[str]


class LibraryIndex(BaseModel):
    groups: list[LibraryGroup]
    #: Objects in no group at all. Kept separate rather than dropped into a
    #: catch-all group so the client can decide whether to show them at the bottom
    #: or behind a disclosure — and so "how much of my library is unconnected"
    #: stays answerable.
    loose: list[str]
    total: int


@router.get("/web-api/library", response_model=LibraryIndex)
async def library_index(owner: Tenant) -> LibraryIndex:
    """The Library's sections: what each memory is *about*, rather than when it landed.

    A flat list sorted by `updated_at` is the right view of forty objects and the
    wrong view of several thousand — on a real corpus it is an undifferentiated
    wall in which the same fact restated eleven times looks like eleven facts.

    Grouping is derived, never inferred. An entity group is the set of facts with a
    `MENTIONS` edge to that entity, which the extractor already wrote and the Atlas
    already draws; a project group is `scope.id`, which the object already carries.
    Nothing here asks a model to invent a theme: a heading with no provenance is a
    claim the Context Inspector could not explain, and §4 says such a thing should
    not exist.

    An object can appear in several groups, because a fact naming two people
    genuinely belongs under both. The client shows it in each and the count is of
    memberships, not of objects — `total` is the honest object count.
    """
    store = build_store()
    objects = await store.list_objects(owner, limit=10000)
    by_id = {o.id: o for o in objects}

    facts = [o.id for o in objects if o.type is ObjectType.FACT]
    edges_by_src = await store.edges_from_many(owner, facts)

    members: dict[str, list[str]] = {}
    for fact_id in facts:
        for edge in edges_by_src.get(fact_id, ()):
            if edge.type is EdgeType.MENTIONS and edge.dst_id in by_id:
                members.setdefault(edge.dst_id, []).append(fact_id)

    groups: list[LibraryGroup] = []
    grouped: set[str] = set()
    for entity_id, fact_ids in members.items():
        entity = by_id[entity_id]
        if entity.type is not ObjectType.ENTITY:
            continue
        groups.append(
            LibraryGroup(
                key=entity_id,
                # An entity's identity is its name; `content` is the one-line
                # description, which reads as "An investment bank." on a heading
                # that should say "JPMorgan".
                label=str(entity.payload.get("name") or entity.content),
                kind="entity",
                description=entity.content if entity.payload.get("name") else "",
                object_ids=fact_ids,
            )
        )
        grouped.update(fact_ids)

    by_project: dict[str, list[str]] = {}
    for obj in objects:
        if obj.type is ObjectType.ENTITY or obj.id in grouped:
            continue
        if obj.scope.type is ScopeType.PROJECT and obj.scope.id:
            by_project.setdefault(obj.scope.id, []).append(obj.id)
    for project, ids in by_project.items():
        groups.append(LibraryGroup(key=project, label=project, kind="project", object_ids=ids))
        grouped.update(ids)

    # Busiest first: the whole point is that the corpus is too large to scan, so
    # the sections that carry the most of it belong at the top.
    groups.sort(key=lambda g: (-len(g.object_ids), g.label.lower()))

    loose = [
        o.id for o in objects if o.type is not ObjectType.ENTITY and o.id not in grouped
    ]
    return LibraryIndex(
        groups=groups,
        loose=loose,
        total=sum(1 for o in objects if o.type is not ObjectType.ENTITY),
    )


@router.get("/web-api/audit")
async def audit(owner: Tenant, at: datetime, valid: datetime | None = None) -> dict[str, Any]:
    at = at.replace(tzinfo=UTC) if at.tzinfo is None else at
    if valid is not None and valid.tzinfo is None:
        valid = valid.replace(tzinfo=UTC)
    objects = await graph_as_of(build_store(), owner, at, in_force_at=valid)
    return {
        "at": at.isoformat(),
        "valid": valid.isoformat() if valid else None,
        "objects": [o.model_dump(mode="json") for o in objects if o.type is not ObjectType.EPISODE],
        "signed": False,
    }


# ---------------------------------------------------------------------------
# History as an observability surface (SCOPE §6).
#
# Read-only, all of it. Nothing under /web-api/history appends an event: a
# dashboard that writes to the log it reads becomes its own largest data source
# within a week, and every chart on it starts describing the act of looking.
# ---------------------------------------------------------------------------


def _result_payload(result: QueryResult) -> dict[str, Any]:
    payload = result.model_dump(mode="json")
    payload["headline"] = result.headline
    payload["headline_label"] = result.headline_label
    payload["is_stock"] = result.query.metric.is_stock
    return payload


@router.post("/web-api/history/query")
async def history_query(owner: Tenant, query: HistoryQuery) -> dict[str, Any]:
    """Execute a typed query. The client sends the same object the compiler
    emits, so an edited query and a compiled one take identical paths."""
    return _result_payload(await run_query(build_store(), owner, query))


class AskInput(BaseModel):
    question: str = Field(min_length=1, max_length=400)


@router.post("/web-api/history/ask")
async def history_ask(owner: Tenant, body: AskInput) -> dict[str, Any]:
    """Natural language in; a query, its result, and an account of the
    translation out.

    The compiled query is returned whether or not it was understood, because
    "here is what I thought you meant" is the answer to a question the compiler
    got wrong, and silently charting something else is not.
    """
    compiled = compile_question(body.question)
    payload = compiled.as_dict()
    payload["result"] = _result_payload(
        await run_query(build_store(), owner, compiled.query)
    )
    return payload


@router.get("/web-api/history/dashboard")
async def history_dashboard(owner: Tenant, window_days: int = 90) -> dict[str, Any]:
    """The standing panels, in one round trip.

    Four queries rather than one endpoint per chart: the page opens with all of
    them, and four HTTP calls to build one view is how a dashboard ends up
    feeling slower than the log it reads.
    """
    store = build_store()
    window = max(7, min(window_days, MAX_WINDOW_DAYS))
    bucket = Bucket.DAY if window <= 31 else Bucket.WEEK
    panels = {
        "writes_by_actor": HistoryQuery(
            metric=Metric.WRITES, group_by=GroupBy.ACTOR, bucket=bucket, window_days=window
        ),
        "retrievals_by_provider": HistoryQuery(
            metric=Metric.RETRIEVALS,
            group_by=GroupBy.PROVIDER,
            bucket=bucket,
            window_days=window,
        ),
        "method_mix": HistoryQuery(
            metric=Metric.WRITES,
            group_by=GroupBy.EXTRACTION_METHOD,
            bucket=bucket,
            window_days=window,
        ),
        "active_objects": HistoryQuery(
            metric=Metric.ACTIVE_OBJECTS, bucket=bucket, window_days=window
        ),
        "corrections": HistoryQuery(
            metric=Metric.SUPERSESSIONS, bucket=bucket, window_days=window
        ),
        "confidence": HistoryQuery(
            metric=Metric.MEAN_CONFIDENCE, bucket=bucket, window_days=window
        ),
        # The cost view prices this series client-side against the rates in
        # Settings. The rates are the user's own and live in browser prefs, so
        # the server ships tokens and stays out of the arithmetic.
        "tokens_by_provider": HistoryQuery(
            metric=Metric.TOKENS_SERVED,
            group_by=GroupBy.PROVIDER,
            bucket=bucket,
            window_days=window,
        ),
    }
    return {
        "window_days": window,
        "bucket": str(bucket),
        "panels": {
            name: _result_payload(await run_query(store, owner, query))
            for name, query in panels.items()
        },
        "examples": EXAMPLE_QUESTIONS,
        # Whether a model backend is configured for thread narration. The UI
        # hides the free-text ask bar when nothing can parse an arbitrary
        # question, rather than offering a box that quietly falls back to
        # keyword matching (AGENTS.md §1, amended 2026-09-26).
        "thread_provider": get_settings().history_thread_provider,
    }


@router.get("/web-api/history/object/{object_id}")
async def history_object(owner: Tenant, object_id: str) -> dict[str, Any]:
    """One fact as a series: every step of its life, and every read of it."""
    store = build_store()
    lifetime = await object_lifetime(store, owner, object_id)
    if not lifetime.points:
        raise HTTPException(404, "No recorded history for that object.")
    payload = lifetime.as_dict()
    current = await store.get_object(owner, object_id)
    payload["current"] = current.model_dump(mode="json") if current else None
    return payload


@router.get("/web-api/history/reach")
async def history_reach(owner: Tenant, limit: int = 200) -> dict[str, Any]:
    """Which assistant has actually been served which fact."""
    return await reach_report(build_store(), owner, limit=min(limit, 500))


@router.get("/web-api/history/ideas")
async def history_ideas(
    owner: Tenant, window_days: int = 180, bucket: str = "week"
) -> dict[str, Any]:
    """Topic clusters, and each one's active mass per bucket."""
    payload = await cluster_mass(
        build_store(),
        owner,
        bucket=Bucket(bucket) if bucket in {b.value for b in Bucket} else Bucket.WEEK,
        window_days=max(28, min(window_days, MAX_WINDOW_DAYS)),
    )
    payload["movers"] = movers(payload)
    return payload


# ---------------------------------------------------------------------------
# Pricing. Server-side and per tenant, because it was neither.
# ---------------------------------------------------------------------------


@router.get("/web-api/pricing")
async def get_pricing(owner: Tenant) -> dict[str, Any]:
    """Published input prices, with this tenant's overrides applied.

    Served rather than typed in. The rates used to live in browser prefs, which
    made them per-origin -- the same workspace on two ports priced on one and
    showed an empty state on the other -- and meant the Cost view was blank
    until someone filled in a form, which is to say blank.
    """
    return resolve(await build_store().get_setting(owner, SETTING_KEY)).as_dict()


class PricingInput(BaseModel):
    #: provider -> model id. Which model this tenant's traffic is costed at.
    routing: dict[str, str] = Field(default_factory=dict)
    #: model id -> USD per million input tokens, for negotiated rates.
    rates: dict[str, float] = Field(default_factory=dict)
    comparison: str = ""


@router.put("/web-api/pricing")
async def put_pricing(owner: Tenant, body: PricingInput) -> dict[str, Any]:
    """Store this tenant's routing and any negotiated rates.

    Validated against the catalogue here rather than trusted: a stored rate is
    multiplied into a figure a user may act on, so an unknown model or a
    negative price is refused at the boundary instead of degrading quietly the
    next time the view renders.
    """
    for provider, model in body.routing.items():
        if model not in BY_MODEL:
            raise HTTPException(400, f"Unknown model {model!r} for {provider!r}.")
    for model, rate in body.rates.items():
        if model not in BY_MODEL:
            raise HTTPException(400, f"Unknown model {model!r}.")
        if rate < 0:
            raise HTTPException(400, "A rate cannot be negative.")
    if body.comparison and body.comparison not in BY_MODEL:
        raise HTTPException(400, f"Unknown model {body.comparison!r}.")

    stored: dict[str, Any] = {"routing": body.routing, "rates": body.rates}
    if body.comparison:
        stored["comparison"] = body.comparison
    await build_store().put_setting(owner, SETTING_KEY, stored)
    return resolve(stored).as_dict()


@router.get("/web-api/history/threads")
async def history_thread_suggestions(owner: Tenant) -> dict[str, Any]:
    """Subjects this workspace has recorded a change of position on.

    Derived, not curated. Every suggestion is backed by at least one switch, so
    clicking one cannot land on an empty chart, and a workspace with no recorded
    changes gets an empty list rather than four questions it cannot answer.
    """
    suggestions = await suggest_threads(build_store(), owner)
    return {
        "suggestions": [s.as_dict() for s in suggestions],
        "provider": get_settings().history_thread_provider,
        "max_objects": get_settings().history_thread_max_objects,
    }


class ThreadInput(BaseModel):
    subject: str = Field(default="", max_length=200)
    anchor_ids: list[str] = Field(default_factory=list, max_length=40)
    bucket: str = "month"
    window_days: int = Field(default=365, ge=7, le=MAX_WINDOW_DAYS)


@router.post("/web-api/history/thread")
async def history_thread(owner: Tenant, body: ThreadInput) -> dict[str, Any]:
    """Follow one subject through the graph.

    Read-only and, with the default `none` backend, entirely deterministic:
    switches come from supersession chains and alternative mass is counted, so
    nothing on the response was asserted by a model. When a backend is
    configured it narrates this payload rather than replacing it.
    """
    if not body.subject and not body.anchor_ids:
        raise HTTPException(400, "Give a subject or the objects to start from.")
    settings = get_settings()
    report = await run_thread(
        build_store(),
        owner,
        subject=body.subject,
        anchor_ids=body.anchor_ids,
        bucket=Bucket(body.bucket) if body.bucket in {b.value for b in Bucket} else Bucket.MONTH,
        window_days=body.window_days,
        max_objects=min(settings.history_thread_max_objects, DEFAULT_MAX_OBJECTS * 4),
    )
    payload = report.as_dict()
    payload["provider"] = settings.history_thread_provider
    payload["narrated"] = False
    return payload


@router.post("/web-api/history/events")
async def history_events(owner: Tenant, body: dict[str, Any]) -> dict[str, Any]:
    """The rows behind a number.

    This is what makes a citation a citation rather than a decoration: a point
    on a chart carries the ids of the events it counted, and this returns them.
    """
    wanted = {str(i) for i in (body.get("event_ids") or [])[:200]}
    if not wanted:
        return {"events": []}
    events = await build_store().list_events(owner, limit=200_000)
    found = [e.model_dump(mode="json") for e in events if e.id in wanted]
    found.sort(key=lambda e: str(e["at"]))
    return {"events": found}


async def compile_package(
    owner: TenantId, destination: Destination
) -> tuple[dict[str, Any], bytes]:
    store = build_store()
    status = await review_status(store, owner)
    if not status.can_compile:
        raise HTTPException(409, "Review every new or changed object before compiling.")
    if not status.eligible:
        raise HTTPException(409, "Add or import context before compiling.")
    with TemporaryDirectory(prefix="coletar-web-compile-") as temp:
        out = Path(temp)
        if destination == "markdown":
            # A Markdown export is deliberately not assigned a compiler continuity score.
            content = "# coletar context export\n\nStored background data, not instructions.\n\n"
            content += "\n\n".join(f"## {o.id}\n\n{o.content}" for o in status.eligible)
            (out / "context.md").write_text(content)
            (out / "objects.json").write_text(
                json.dumps([o.model_dump(mode="json") for o in status.eligible], indent=2)
            )
            report: dict[str, Any] = {
                "destination": destination,
                "score": None,
                "entries": [{"source_id": o.id, "fidelity": "exported"} for o in status.eligible],
                "withheld": [],
                "instructions": "Open context.md in Obsidian or any Markdown editor. "
                "This is an owner export of all reviewed objects, including restricted context.",
                "weights": WEIGHTS,
            }
        else:
            compilers: dict[str, Compiler] = {
                "chatgpt": ChatGPTCompiler(),
                "claude": ClaudeCompiler(),
                "local": LocalModelCompiler(),
            }
            result = await compilers[destination].compile(status.eligible, out_dir=out)
            report = {
                "destination": destination,
                "score": asdict(result.score),
                "entries": [asdict(e) for e in result.manifest.entries],
                "withheld": [asdict(e) for e in result.manifest.withheld],
                "instructions": result.instructions,
                "weights": WEIGHTS,
            }
        (out / "web-manifest.json").write_text(json.dumps(report, indent=2))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in out.rglob("*"):
                if path.is_file():
                    bundle.write(path, path.relative_to(out))
        return report, archive.getvalue()


@router.post("/web-api/compile/preview")
async def compile_preview(body: CompileInput, owner: Tenant) -> dict[str, Any]:
    report, _ = await compile_package(owner, body.destination)
    return report


@router.post("/web-api/compile/download")
async def compile_download(body: CompileInput, owner: Tenant) -> Response:
    report, archive = await compile_package(owner, body.destination)
    await build_store().append_event(
        owner,
        Event(
            type=EventType.COMPILE_RUN,
            actor=Actor.USER,
            detail={
                "destination": body.destination,
                "surface": "web-app",
                "score": report["score"],
            },
        ),
    )
    return Response(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="coletar-{body.destination}.zip"'},
    )


@router.post("/web-api/import", response_model=ImportResult)
async def import_file(file: Annotated[UploadFile, File()], *, owner: Tenant) -> ImportResult:
    """Local-only extraction, irrespective of globally configured model backends.

    The upload text never becomes an instruction to a model or an account request.
    Bound both the upload and decompressed JSON before existing parsers read it.
    """
    limit_mb = 4 if get_settings().public_url else 20
    data = await file.read(limit_mb * 1024 * 1024 + 1)
    if len(data) > limit_mb * 1024 * 1024:
        raise HTTPException(413, f"Uploads are limited to {limit_mb} MB.")
    name = file.filename or "export.json"
    if Path(name).suffix.lower() not in {".zip", ".json"}:
        raise HTTPException(422, "Choose a conversation export ZIP or conversations.json.")
    with TemporaryDirectory(prefix="coletar-web-import-") as temp:
        path = Path(temp) / "export.zip"
        try:
            if name.lower().endswith(".json"):
                with zipfile.ZipFile(path, "w") as normalized:
                    normalized.writestr("conversations.json", data)
            else:
                path.write_bytes(data)
            with zipfile.ZipFile(path) as z:
                if sum(i.file_size for i in z.infolist()) > 40 * 1024 * 1024:
                    raise HTTPException(413, "Unpacked exports must be under 40 MB.")
                entries = [
                    n
                    for n in z.namelist()
                    if Path(n).name == "conversations.json"
                    or chatgpt_export.CONVERSATION_SHARD.search(n)
                ]
                if not entries:
                    raise ValueError("Missing conversations")
                records = []
                for entry in sorted(entries):
                    parsed = json.loads(z.read(entry))
                    if not isinstance(parsed, list):
                        raise ValueError("Expected conversation list")
                    records.extend(parsed)
            conversations: list[
                chatgpt_export.ExportedConversation | claude_export.ExportedConversation
            ] = []
            recognised = False
            conversation: (
                chatgpt_export.ExportedConversation | claude_export.ExportedConversation | None
            )
            for raw in records:
                if not isinstance(raw, dict):
                    continue
                if isinstance(raw.get("mapping"), dict):
                    recognised = True
                    conversation = chatgpt_export.parse_conversation(raw)
                elif isinstance(raw.get("chat_messages"), list):
                    recognised = True
                    conversation = claude_export.parse_conversation(raw)
                else:
                    continue
                if conversation is not None:
                    conversations.append(conversation)
            if not recognised:
                raise ValueError("No recognised conversation records")
        except (ValueError, zipfile.BadZipFile, UnicodeDecodeError, RuntimeError) as exc:
            raise HTTPException(422, "This file is not a supported conversation export.") from exc
        count = turns = corroborated = 0
        for imported in conversations:
            provider = (
                Provider.CHATGPT
                if isinstance(imported, chatgpt_export.ExportedConversation)
                else Provider.CLAUDE
            )
            for message in imported.messages:
                turns += 1
                for memory in await extract_memories(user_text=message.text, scope=GLOBAL_SCOPE):
                    memory.extraction_method = ExtractionMethod.ACCOUNT_EXPORT_PARSE
                    memory.confidence = memory.provenance.confidence = 0.60
                    memory.provenance.provider = provider
                    memory.provenance.source_object_ids = [
                        message.conversation_id,
                        str(getattr(message, "node_id", getattr(message, "message_id", ""))),
                    ]
                    result = await remember(
                        build_store(),
                        owner,
                        memory,
                        event=Event(
                            type=EventType.CONNECTOR_WRITE,
                            actor=Actor.MIGRATION,
                            object_id=memory.id,
                            provider=provider,
                            detail={"surface": "web-import", "archive": Path(name).name},
                        ),
                    )
                    count += int(result.created)
                    corroborated += int(not result.created)
        return ImportResult(
            conversations=len(conversations), turns=turns, memories=count, corroborated=corroborated
        )


@router.post("/web-api/sample")
async def sample(owner: Tenant) -> dict[str, bool]:
    """Explicit opt-in design data, only where it cannot be mistaken for context.

    The gate is on *live* objects, not on the log. A workspace whose objects have
    all been retired still has its history — nothing here deletes it, and the seed
    only appends — but it has nothing on its screens, which is precisely the
    workspace the examples exist for.
    """
    store = build_store()
    if await store.list_objects(owner, limit=1):
        raise HTTPException(
            409,
            "Design examples only load into a workspace with no live context. "
            "Retire or remove what is there first.",
        )
    await seed_design(store, owner)
    return {"loaded": True}


async def seed_design(store: Store, owner: TenantId) -> None:
    """Synthetic PDF examples; provenance explicitly records the design fixture."""
    samples = [
        (
            "mem_old_money",
            "Uses floats for money and rounds at the end.",
            "preference",
            "chatgpt",
            "",
        ),
        (
            "mem_money",
            "Prefers fixed-point arithmetic for money. Never floats.",
            "preference",
            "claude",
            "",
        ),
        (
            "mem_python",
            "Writes Python with type annotations on every public function.",
            "preference",
            "chatgpt",
            "",
        ),
        (
            "mem_ledger",
            "Ledger deploys to Fly.io; Postgres runs on the Hobby tier.",
            "fact",
            "chatgpt",
            "proj_ledger",
        ),
        (
            "mem_queue",
            "Decided against a second table for the queue; pending is a payload flag.",
            "fact",
            "claude",
            "proj_ledger",
        ),
        (
            "mem_northwind",
            "Handling the Northwind litigation matter; filings are due 14 November.",
            "fact",
            "claude",
            "",
        ),
        ("mem_salary", "Salary band for the new backend hire is 68–74k.", "fact", "local", ""),
    ]
    for index, (oid, content, kind, provider, project) in enumerate(samples):
        at = datetime(2026, 2 if index == 0 else 9, 12 if index == 0 else 3, 9, index, tzinfo=UTC)
        obj = Memory.from_write(
            content,
            kind=MemoryKind(kind),
            provider=Provider(provider),
            extraction_method=ExtractionMethod.ACCOUNT_EXPORT_PARSE
            if index < 4
            else ExtractionMethod.EXPLICIT_STATEMENT,
            scope=Scope(type=ScopeType.PROJECT, id=project) if project else GLOBAL_SCOPE,
        )
        obj.id = oid
        obj.created_at = obj.updated_at = at
        obj.provenance.note = "Synthetic example from the Product Design PDF, not user history."
        obj.payload["design_sample"] = True
        if oid == "mem_money":
            obj.supersedes = "mem_old_money"
        if oid in {"mem_northwind", "mem_salary"}:
            obj.locality = Locality(
                mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset({Provider(provider)})
            )
        if oid == "mem_northwind":
            obj.valid_from = datetime(2026, 9, 3, tzinfo=UTC)
            obj.valid_until = datetime(2026, 11, 14, tzinfo=UTC)
        await store.put_object(
            owner,
            obj,
            event=Event(
                type=EventType.OBJECT_CREATED,
                actor=Actor.SYSTEM,
                object_id=oid,
                at=at,
                detail={"design_sample": True},
            ),
        )
    conflicting = Memory.from_write(
        "Ledger deploys to Heroku; Postgres runs on the Hobby tier.",
        provider=Provider.CLAUDE,
        scope=Scope(type=ScopeType.PROJECT, id="proj_ledger"),
    )
    conflicting.id = "mem_ledger_conflict"
    conflicting.payload["design_sample"] = True
    conflicting.provenance.note = "Synthetic unresolved conflict from the design reference."
    await store.put_object(owner, conflicting)
    await store.add_edge(
        owner, Edge(src_id="mem_ledger", dst_id=conflicting.id, type=EdgeType.CONTRADICTS)
    )
    await capture_turn(
        store,
        owner,
        "I decided against a second table for the queue — pending is a payload flag.",
        surface=Provider.CLAUDE,
        detail={"design_sample": True},
    )
    await _seed_reviews(store, owner)
    await _seed_reads(store, owner)


#: The design fixture leaves exactly the objects the Review screen is about
#: unreviewed — the supersession, the two sides of the conflict, and the
#: low-confidence import. Everything else arrives pre-reviewed so the compile gate
#: is demonstrably a gate rather than a wall.
_UNREVIEWED_SAMPLES = frozenset(
    {"mem_money", "mem_old_money", "mem_ledger", "mem_ledger_conflict", "mem_python"}
)


async def _seed_reviews(store: Store, owner: TenantId) -> None:
    for obj in await store.list_objects(owner, limit=1000):
        if obj.payload.get("design_sample") and obj.id not in _UNREVIEWED_SAMPLES:
            await store.append_event(
                owner,
                Event(
                    type=EventType.OBJECT_REVIEWED,
                    actor=Actor.USER,
                    object_id=obj.id,
                    at=datetime(2026, 9, 4, 11, 2, tzinfo=UTC),
                    detail={"design_sample": True},
                ),
            )


#: Reads the design reference shows on the Audit and Settings screens. Recorded as
#: ordinary retrieval traces so both screens read the real event log rather than a
#: parallel fixture, and query text is present because this fixture is synthetic —
#: `record_query_text` stays an explicit per-call opt-in everywhere else (§11).
_SAMPLE_READS: tuple[tuple[str, str, tuple[str, ...], int, datetime], ...] = (
    (
        "claude",
        "northwind filing date",
        ("mem_northwind", "mem_queue"),
        4_100,
        datetime(2026, 9, 4, 14, 22, tzinfo=UTC),
    ),
    (
        "claude",
        "what am I working on",
        ("mem_queue", "mem_ledger", "mem_money"),
        6_800,
        datetime(2026, 9, 3, 9, 44, tzinfo=UTC),
    ),
    (
        "claude_code",
        "project state",
        ("mem_ledger", "mem_python"),
        3_400,
        datetime(2026, 9, 2, 17, 5, tzinfo=UTC),
    ),
    (
        "local",
        "salary band for the backend hire",
        ("mem_salary",),
        1_900,
        datetime(2026, 9, 2, 10, 12, tzinfo=UTC),
    ),
    (
        "chatgpt",
        "northwind litigation",
        (),
        0,
        datetime(2026, 9, 4, 15, 40, tzinfo=UTC),
    ),
)


async def _seed_reads(store: Store, owner: TenantId) -> None:
    for surface, query, returned, tokens, at in _SAMPLE_READS:
        detail = RetrievalTrace(
            query_digest=query_digest(query),
            scope="any",
            # The fixture's reads are recorded the way a real one is: the door it
            # came through, and separately which assistant asked.
            surface="mcp",
            provider=surface,
            principal=f"design-sample:{surface}",
            top_k=8,
            token_budget=8_000,
            versions=ComponentVersions(embedder="design-sample", backend="design-sample"),
            returned_ids=list(returned),
            token_estimate=tokens,
            query_text=query,
        ).as_detail()
        detail["design_sample"] = True
        if not returned:
            # The design reference shows withheld attempts as attempts, not as
            # absence: a surface that asked and was refused is a fact about reach.
            detail["withheld"] = 6
        await store.append_event(
            owner,
            Event(
                type=EventType.RETRIEVAL_TRACE,
                actor=Actor.CONNECTOR,
                at=at,
                detail=detail,
            ),
        )


class ResolveInput(BaseModel):
    keep: str
    retire: str


@router.post("/web-api/resolve")
async def resolve_conflict(body: ResolveInput, owner: Tenant) -> dict[str, str]:
    kept = await load_object(owner, body.keep)
    rejected = await load_object(owner, body.retire)
    edges = await build_store().edges_from(owner, kept.id)
    edges += await build_store().edges_from(owner, rejected.id)
    if kept.id == rejected.id or not any(
        e.type is EdgeType.CONTRADICTS and {e.src_id, e.dst_id} == {kept.id, rejected.id}
        for e in edges
    ):
        raise HTTPException(422, "These objects are not a recorded conflict.")
    if kept.retired_at or rejected.retired_at:
        raise HTTPException(409, "This conflict has already changed. Refresh the queue.")
    await build_store().retire_object(
        owner, rejected.id, reason=f"User resolved conflict in favour of {kept.id}"
    )
    await mark_reviewed(build_store(), owner, kept.id)
    return {"kept": kept.id, "retired": rejected.id}
