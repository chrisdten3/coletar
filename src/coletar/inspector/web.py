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

from coletar.acquisition import chatgpt_export, claude_export
from coletar.capture import capture_turn, is_pending
from coletar.compiler import ChatGPTCompiler, ClaudeCompiler, LocalModelCompiler
from coletar.compiler.base import Compiler
from coletar.compiler.continuity import WEIGHTS
from coletar.config import get_settings
from coletar.episode_crypto import EpisodeKeyUnavailable, decrypt_episode
from coletar.extraction import extract_memories
from coletar.ingest import remember
from coletar.inspector.review import edit, mark_reviewed, review_status
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
from coletar.schema.tenancy import TenantId, tenant_id
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


def tenant() -> TenantId:
    return tenant_id(get_settings().default_tenant_id)


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


async def load_object(object_id: str) -> ContextObject:
    obj = await build_store().get_object(tenant(), object_id)
    if obj is None:
        raise HTTPException(404, "Object not found in this workspace")
    return obj


async def safe_object(store: Store, obj: ContextObject) -> dict[str, Any]:
    result = obj.model_dump(mode="json")
    if obj.type is ObjectType.EPISODE:
        try:
            result["content"] = await decrypt_episode(store, tenant(), obj)
        except EpisodeKeyUnavailable:
            result["content"] = "This captured turn has been erased."
        result["pending"] = is_pending(obj)
    return result


_STATIC = Path(__file__).parent / "static"


@lru_cache(maxsize=1)
def _app_html() -> str:
    """The shell, stamped with a digest of its own assets.

    Browsers cache /static aggressively, and a deploy that changes the client
    without changing its URL is a deploy that reaches nobody.
    """
    digest = sha256()
    for name in ("product.css", "product.js"):
        digest.update((_STATIC / name).read_bytes())
    return (_STATIC / "product.html").read_text().replace("__ASSETS__", digest.hexdigest()[:12])


@router.get("/app", include_in_schema=False)
async def product_app() -> HTMLResponse:
    return HTMLResponse(_app_html())


@router.get("/web-api/state", response_model=Snapshot)
async def state() -> Snapshot:
    store = build_store()
    status = await review_status(store, tenant())
    objects = await store.list_objects(
        tenant(), include_retired=True, include_superseded=True, limit=10000
    )
    events = await store.list_events(tenant(), limit=2000)
    usage: dict[str, int] = {}
    for event in events:
        if event.type is EventType.RETRIEVAL_TRACE:
            surface = str(event.detail.get("surface", "unknown"))
            usage[surface] = usage.get(surface, 0) + int(event.detail.get("token_estimate", 0))
    conflicts = []
    for obj in objects:
        for edge in await store.edges_from(tenant(), obj.id):
            if edge.type is EdgeType.CONTRADICTS:
                conflicts.append((edge.src_id, edge.dst_id))
    return Snapshot(
        hosted=bool(get_settings().public_url),
        conflicts=conflicts,
        tenant=str(tenant()),
        objects=[await safe_object(store, o) for o in objects],
        unreviewed=[o.id for o in status.unreviewed],
        can_compile=status.can_compile,
        # Events can contain encrypted raw turns, but never their content keys.
        events=[e.model_dump(mode="json") for e in events],
        usage=usage,
        sample=any(o.payload.get("design_sample") for o in objects),
    )


@router.post("/web-api/memories")
async def add_memory(body: MemoryInput) -> dict[str, str]:
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
        tenant(),
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
async def object_action(object_id: str, body: ActionInput) -> dict[str, str]:
    obj = await load_object(object_id)
    if obj.type is ObjectType.EPISODE:
        raise HTTPException(422, "Raw captured turns are source evidence, not editable memories.")
    if obj.retired_at is not None:
        raise HTTPException(409, "This object is retired. Its history remains readable.")
    store = build_store()
    if body.action == "review":
        await mark_reviewed(store, tenant(), object_id)
    elif body.action == "retire":
        await store.retire_object(tenant(), object_id, reason="Retired by user in web app")
    elif body.action == "edit":
        if not body.content.strip():
            raise HTTPException(422, "Content cannot be empty. Retire the object instead.")
        await edit(store, tenant(), object_id, content=body.content)
    else:
        if body.locality != obj.locality:
            before = str(obj.locality)
            obj.locality = body.locality
            await store.put_object(
                tenant(),
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
async def review_many(body: ReviewInput) -> dict[str, int]:
    status = await review_status(build_store(), tenant())
    eligible = {o.id for o in status.unreviewed}
    if not set(body.ids).issubset(eligible):
        raise HTTPException(409, "The review queue changed. Refresh it and try again.")
    for object_id in set(body.ids):
        await mark_reviewed(build_store(), tenant(), object_id)
    return {"reviewed": len(set(body.ids))}


@router.get("/web-api/audit")
async def audit(at: datetime, valid: datetime | None = None) -> dict[str, Any]:
    at = at.replace(tzinfo=UTC) if at.tzinfo is None else at
    if valid is not None and valid.tzinfo is None:
        valid = valid.replace(tzinfo=UTC)
    objects = await graph_as_of(build_store(), tenant(), at, in_force_at=valid)
    return {
        "at": at.isoformat(),
        "valid": valid.isoformat() if valid else None,
        "objects": [o.model_dump(mode="json") for o in objects if o.type is not ObjectType.EPISODE],
        "signed": False,
    }


async def compile_package(destination: Destination) -> tuple[dict[str, Any], bytes]:
    store = build_store()
    status = await review_status(store, tenant())
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
async def compile_preview(body: CompileInput) -> dict[str, Any]:
    report, _ = await compile_package(body.destination)
    return report


@router.post("/web-api/compile/download")
async def compile_download(body: CompileInput) -> Response:
    report, archive = await compile_package(body.destination)
    await build_store().append_event(
        tenant(),
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
async def import_file(file: Annotated[UploadFile, File()]) -> ImportResult:
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
                        tenant(),
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
async def sample() -> dict[str, bool]:
    """Explicit opt-in design data, only where it cannot be mistaken for context.

    The gate is on *live* objects, not on the log. A workspace whose objects have
    all been retired still has its history — nothing here deletes it, and the seed
    only appends — but it has nothing on its screens, which is precisely the
    workspace the examples exist for.
    """
    store = build_store()
    if await store.list_objects(tenant(), limit=1):
        raise HTTPException(
            409,
            "Design examples only load into a workspace with no live context. "
            "Retire or remove what is there first.",
        )
    await seed_design(store, tenant())
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
            surface=surface,
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
async def resolve_conflict(body: ResolveInput) -> dict[str, str]:
    kept = await load_object(body.keep)
    rejected = await load_object(body.retire)
    edges = await build_store().edges_from(tenant(), kept.id)
    edges += await build_store().edges_from(tenant(), rejected.id)
    if kept.id == rejected.id or not any(
        e.type is EdgeType.CONTRADICTS and {e.src_id, e.dst_id} == {kept.id, rejected.id}
        for e in edges
    ):
        raise HTTPException(422, "These objects are not a recorded conflict.")
    if kept.retired_at or rejected.retired_at:
        raise HTTPException(409, "This conflict has already changed. Refresh the queue.")
    await build_store().retire_object(
        tenant(), rejected.id, reason=f"User resolved conflict in favour of {kept.id}"
    )
    await mark_reviewed(build_store(), tenant(), kept.id)
    return {"kept": kept.id, "retired": rejected.id}
