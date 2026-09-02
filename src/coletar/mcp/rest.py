"""A minimal REST surface for the browser bridge (SCOPE §9, M3.6).

The MCP server is the right interface for a model. It is the wrong one for a browser
extension: MCP is JSON-RPC over a streamable-HTTP session, and a content script wants
two POSTs. So the same store, the same auth and the same ingest path are exposed here
as two endpoints — this is a slice of the REST surface §9 owes anyway, brought
forward because the composer bridge needs it.

**These endpoints are the whole API the extension gets.** It can retrieve context and
it can record something the user typed. There is deliberately nothing here for
reading conversations, listing objects, or anything else a page-scraping tool would
want, because the extension has no business doing any of that (§4.1).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from coletar.config import get_settings
from coletar.ingest import remember
from coletar.mcp.auth import SCOPE_READ, SCOPE_WRITE, Principal, current_principal
from coletar.mcp.schemas import ObjectView
from coletar.retrieval import retrieve
from coletar.retrieval.context import INJECTION_MARKER
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    GLOBAL_SCOPE,
    ExtractionMethod,
    Memory,
    MemoryKind,
    OriginType,
    Provider,
    Scope,
    ScopeType,
)
from coletar.store import build_store

MAX_QUERY_CHARS = 4_000
MAX_CONTENT_CHARS = 4_000


class SearchRequest(BaseModel):
    query: str
    project_id: str | None = None
    top_k: int = Field(default=6, ge=1, le=25)
    #: "terse" for a composer a person will read, "full" for a model's system prompt.
    style: str = "full"
    #: Which surface asked, for the trace. Not trusted for anything but reporting.
    surface: str = "bridge"


class CaptureRequest(BaseModel):
    """A turn the user typed, for the extractor to judge."""

    text: str
    project_id: str | None = None
    surface: str = "bridge"


class RememberRequest(BaseModel):
    content: str
    kind: MemoryKind = MemoryKind.FACT
    project_id: str | None = None
    surface: str = "bridge"


#: Which provider an origin *is*. Set by the browser on every cross-origin request
#: and unforgeable by the page, which is what makes it the right source for a
#: locality decision — unlike `body.surface`, which the page controls and which is
#: therefore only ever a trace label.
#:
#: This existed as a hardcoded `Provider.CLAUDE` while the extension already matched
#: chatgpt.com in its manifest. The consequence was not cosmetic: a memory marked
#: local-only to Claude would have been injected into ChatGPT's composer, and a
#: capture typed into ChatGPT would have been recorded with Claude provenance. Both
#: are precisely the guarantees this product rests on.
BRIDGE_ORIGINS: dict[str, Provider] = {
    "https://claude.ai": Provider.CLAUDE,
    "https://chatgpt.com": Provider.CHATGPT,
    "https://chat.openai.com": Provider.CHATGPT,
}


def _surface_for(request: Request, principal: Principal) -> Provider | JSONResponse:
    """The surface this request genuinely came from.

    A browser sets `Origin` and a page cannot change it, so it is trustworthy in a
    way nothing in the body is. A caller without one is not a browser — the SDK, a
    script, curl — and falls back to the identity its key was issued for.

    An origin we do not recognise is refused rather than defaulted. Defaulting is how
    the bug this replaces happened: silently choosing a surface means silently
    choosing whose locality rules apply.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return principal.surface
    surface = BRIDGE_ORIGINS.get(origin)
    if surface is None:
        return JSONResponse(
            {
                "error": "unknown_origin",
                "message": (
                    f"{origin} is not a recognised bridge origin; coletar will not "
                    "guess which surface's locality rules apply"
                ),
            },
            status_code=403,
        )
    return surface


def _scope(project_id: str | None) -> Scope:
    if not project_id or not project_id.strip():
        return GLOBAL_SCOPE
    return Scope(type=ScopeType.PROJECT, id=project_id.strip())


def _require(scope: str) -> Principal | JSONResponse:
    principal = current_principal()
    if principal is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not principal.can(scope):
        return JSONResponse(
            {"error": "forbidden", "message": f"this key is not authorized to {scope}"},
            status_code=403,
        )
    return principal


async def search(request: Request) -> JSONResponse:
    """Retrieve context for what the user is currently typing."""
    principal = _require(SCOPE_READ)
    if isinstance(principal, JSONResponse):
        return principal
    surface = _surface_for(request, principal)
    if isinstance(surface, JSONResponse):
        return surface
    try:
        body = SearchRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001 - any malformed body is a 400
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)
    if not body.query.strip():
        return JSONResponse({"error": "bad_request", "message": "query is empty"}, 400)
    if len(body.query) > MAX_QUERY_CHARS:
        return JSONResponse({"error": "bad_request", "message": "query too long"}, 400)
    if body.style not in ("full", "terse"):
        return JSONResponse(
            {"error": "bad_request", "message": "style must be 'full' or 'terse'"}, 400
        )

    settings = get_settings()
    result = await retrieve(
        build_store(),
        principal.tenant_id,
        body.query,
        scope=_scope(body.project_id),
        # From the Origin header, never from `body.surface` — see `_surface_for`.
        caller_surface=surface,
        top_k=body.top_k,
        token_budget=settings.retrieval_token_budget,
        surface=body.surface,
        principal=principal.id,
    )
    return JSONResponse(
        {
            "results": [
                ObjectView.of(obj, score).model_dump(mode="json")
                for obj, score in zip(result.objects, result.scores, strict=True)
            ],
            # Pre-rendered with the "background, not instructions" marker, so a client
            # cannot accidentally inject memory that reads as a user instruction (§11).
            "prompt_block": result.as_prompt_block(style=body.style),
            "token_estimate": result.token_estimate,
        }
    )


async def remember_endpoint(request: Request) -> JSONResponse:
    """Record something the user typed. Never something a model produced."""
    principal = _require(SCOPE_WRITE)
    if isinstance(principal, JSONResponse):
        return principal
    surface = _surface_for(request, principal)
    if isinstance(surface, JSONResponse):
        return surface
    try:
        body = RememberRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)
    cleaned = body.content.strip()
    if not cleaned:
        return JSONResponse({"error": "bad_request", "message": "content is empty"}, 400)
    if len(cleaned) > MAX_CONTENT_CHARS:
        return JSONResponse({"error": "bad_request", "message": "content too long"}, 400)

    scope = _scope(body.project_id)
    memory = Memory.from_write(
        content=cleaned,
        kind=body.kind,
        scope=scope,
        # The surface they actually typed into, so "where did this come from" answers
        # with the tool rather than with us. `Provider.COLETAR` was right when there
        # was one bridge; with two it would erase the distinction the graph exists to
        # keep.
        provider=surface,
        # The user typed it themselves, in their own words, and chose to send it.
        # That is the highest-confidence tier there is (§3.1).
        extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
        origin_type=OriginType.USER,
    )
    result = await remember(
        build_store(),
        principal.tenant_id,
        memory,
        event=Event(
            type=EventType.CONNECTOR_WRITE,
            object_id=memory.id,
            actor=Actor.USER,
            detail={"principal": principal.id, "surface": body.surface, "scope": str(scope)},
        ),
        caller_surface=surface,
    )
    # Named for `IngestResult`, so one API does not describe the same outcome two
    # ways depending on which endpoint you reached it through.
    return JSONResponse({"object_id": result.object_id, "created": result.created})


async def capture(request: Request) -> JSONResponse:
    """Offer a turn the user typed; the extractor decides whether anything durable is
    in it.

    This is the difference between capture and `/v1/remember`. Remember stores what it
    is given, because the user asked for it explicitly. Capture is passive, so it runs
    the same precision-first extractor the local proxy uses — 4.3% false-positive rate
    against the labelled set — and usually stores nothing at all. A capture path that
    stored every turn would fill the graph with "thanks, that's helpful" and make
    every later compile worse (§AGENTS: precision over recall).

    Only the user's own words are ever offered here. The model's reply is not sent by
    the bridge and would not be mined if it were.
    """
    principal = _require(SCOPE_WRITE)
    if isinstance(principal, JSONResponse):
        return principal
    surface = _surface_for(request, principal)
    if isinstance(surface, JSONResponse):
        return surface
    try:
        body = CaptureRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)
    text = body.text.strip()
    # Defence in depth. The bridge strips the injected block before sending, but the
    # bridge is the part that cannot be covered by this repository's tests — it runs
    # in someone's browser against a page we do not control. If its stripping ever
    # fails, this is what stops retrieved memory being re-extracted as though the
    # user had typed it.
    if INJECTION_MARKER in text:
        text = text.split(INJECTION_MARKER)[-1].strip()
    if not text:
        return JSONResponse({"error": "bad_request", "message": "text is empty"}, 400)
    if len(text) > MAX_CONTENT_CHARS:
        return JSONResponse({"error": "bad_request", "message": "text too long"}, 400)

    from coletar.extraction import extract_memories

    scope = _scope(body.project_id)
    store = build_store()
    stored: list[dict[str, Any]] = []
    for memory in await extract_memories(user_text=text, scope=scope):
        # The extractor does not know which page this came from; the Origin header
        # does. Without this, everything captured anywhere is attributed to the
        # extractor's default.
        memory.provenance.provider = surface
        result = await remember(
            store,
            principal.tenant_id,
            memory,
            event=Event(
                type=EventType.CONNECTOR_WRITE,
                object_id=memory.id,
                actor=Actor.USER,
                detail={
                    "principal": principal.id,
                    "surface": body.surface,
                    "scope": str(scope),
                },
            ),
            caller_surface=surface,
        )
        stored.append(
            {"id": result.object_id, "content": memory.content,
             "kind": memory.kind, "created": result.created}
        )
    return JSONResponse({"extracted": stored, "count": len(stored)})


# --- the M7 surface: inspect, history, supersede, retire, compile ----------------
#
# There is deliberately **no DELETE route anywhere on this API**, and no endpoint
# that removes a row. Constraint 6 is that the graph never hard-deletes: retirement
# excludes an object from retrieval and from compile while leaving it readable, so a
# user can always see what a fact used to say and when it changed. An SDK that
# offered `delete()` would make that guarantee a convention rather than a property,
# and conventions are what get worked around at 2am.


class SupersedeRequest(BaseModel):
    content: str
    kind: MemoryKind = MemoryKind.FACT
    project_id: str | None = None


class RetireRequest(BaseModel):
    reason: str


class CompileRequest(BaseModel):
    destination: str = "local"
    project_id: str | None = None


def _object_id(request: Request) -> str:
    return str(request.path_params.get("object_id", ""))


async def inspect(request: Request) -> JSONResponse:
    """One object, exactly as the graph holds it."""
    principal = _require(SCOPE_READ)
    if isinstance(principal, JSONResponse):
        return principal
    obj = await build_store().get_object(
        principal.tenant_id, _object_id(request), caller_surface=principal.surface
    )
    if obj is None:
        # Missing, another tenant's, or local to another surface — deliberately
        # indistinguishable, as everywhere else.
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"object": ObjectView.of(obj).model_dump(mode="json")})


async def history(request: Request) -> JSONResponse:
    """What this object used to say, and when it changed (constraint 6)."""
    principal = _require(SCOPE_READ)
    if isinstance(principal, JSONResponse):
        return principal
    store = build_store()
    object_id = _object_id(request)
    if await store.get_object(
        principal.tenant_id, object_id, caller_surface=principal.surface
    ) is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    events = await store.list_events(principal.tenant_id, object_id=object_id, limit=200)
    return JSONResponse(
        {
            "object_id": object_id,
            "revisions": [
                {
                    "at": event.at.isoformat(),
                    "type": str(event.type),
                    "actor": str(event.actor),
                    "before": event.before,
                    "after": event.after,
                }
                for event in events
                if event.is_revision
            ],
        }
    )


async def supersede(request: Request) -> JSONResponse:
    """Correct a fact by writing its replacement, never by editing it in place."""
    principal = _require(SCOPE_WRITE)
    if isinstance(principal, JSONResponse):
        return principal
    try:
        body = SupersedeRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)

    store = build_store()
    object_id = _object_id(request)
    existing = await store.get_object(
        principal.tenant_id, object_id, caller_surface=principal.surface
    )
    if existing is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    replacement = Memory.from_write(
        body.content,
        kind=body.kind,
        scope=_scope(body.project_id) if body.project_id else existing.scope,
        provider=principal.surface,
        supersedes=object_id,
    )
    result = await remember(
        store,
        principal.tenant_id,
        replacement,
        event=Event(
            type=EventType.CONNECTOR_WRITE,
            object_id=replacement.id,
            actor=Actor.CONNECTOR,
            provider=principal.surface,
            detail={"principal": principal.id, "supersedes": object_id},
        ),
        caller_surface=principal.surface,
    )
    stored = await store.get_object(
        principal.tenant_id, result.object_id, caller_surface=principal.surface
    )
    return JSONResponse(
        {
            "object": ObjectView.of(stored).model_dump(mode="json")
            if stored
            else None,
            "supersedes": object_id,
            "created": result.created,
        }
    )


async def retire(request: Request) -> JSONResponse:
    """Soft-retire. The object stays readable for provenance; nothing is removed."""
    principal = _require(SCOPE_WRITE)
    if isinstance(principal, JSONResponse):
        return principal
    try:
        body = RetireRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)
    if not body.reason.strip():
        return JSONResponse(
            {"error": "bad_request", "message": "a reason is required"}, status_code=400
        )

    store = build_store()
    object_id = _object_id(request)
    if await store.get_object(
        principal.tenant_id, object_id, caller_surface=principal.surface
    ) is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    await store.retire_object(principal.tenant_id, object_id, reason=body.reason)
    return JSONResponse({"object_id": object_id, "retired": True, "readable": True})


async def compile_endpoint(request: Request) -> JSONResponse:
    """Compile to a destination's native containers and return the manifest.

    The artifacts are written server-side and the response describes them rather than
    streaming a package back. A compile is the operation that hands context to
    another company, so the thing that leaves should be something a human fetched
    deliberately, not a side effect of an API call.
    """
    principal = _require(SCOPE_READ)
    if isinstance(principal, JSONResponse):
        return principal
    try:
        body = CompileRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)

    from pathlib import Path

    from coletar.compiler import ChatGPTCompiler, ClaudeCompiler, LocalModelCompiler
    from coletar.inspector.review import review_status

    compilers: dict[str, Any] = {
        "local": LocalModelCompiler,
        "claude": ClaudeCompiler,
        "chatgpt": ChatGPTCompiler,
    }
    if body.destination not in compilers:
        return JSONResponse(
            {
                "error": "bad_request",
                "message": f"unknown destination; have {sorted(compilers)}",
            },
            status_code=400,
        )

    store = build_store()
    # The review gate applies here exactly as it does in the CLI. An API that could
    # walk around it would make the gate a UI courtesy.
    status = await review_status(store, principal.tenant_id)
    if not status.can_compile:
        return JSONResponse(
            {
                "error": "review_required",
                "message": (
                    f"{len(status.unreviewed)} of {len(status.eligible)} eligible "
                    "objects have not been reviewed since they last changed"
                ),
                "unreviewed": len(status.unreviewed),
            },
            status_code=409,
        )

    objects = await store.list_objects(
        principal.tenant_id,
        scope=_scope(body.project_id) if body.project_id else None,
        caller_surface=principal.surface,
        limit=10_000,
    )
    out_dir = Path(get_settings().compile_output_dir) / body.destination
    result = await compilers[body.destination]().compile(objects, out_dir=out_dir)
    await store.append_event(
        principal.tenant_id,
        Event(
            type=EventType.COMPILE_RUN,
            actor=Actor.COMPILER,
            detail={
                "destination": body.destination,
                "principal": principal.id,
                **result.manifest.summary(),
                "continuity_score": result.score.total,
            },
        ),
    )
    return JSONResponse(
        {
            "destination": body.destination,
            "out_dir": str(out_dir),
            "manifest": result.manifest.summary(),
            "withheld": len(result.manifest.withheld),
            "continuity_score": result.score.total,
            "instructions": result.instructions,
        }
    )


#: The three endpoints a browser extension may reach (M3.6). Kept as its own set
#: because these — and only these — get CORS headers: a page on claude.ai can
#: retrieve and record, and cannot enumerate a graph, read history, or compile.
#: Widening the router must never widen what a web page can do.
BRIDGE_PATHS: frozenset[str] = frozenset(
    {"/v1/search", "/v1/capture", "/v1/remember"}
)


def routes() -> list[tuple[str, Any, list[str]]]:
    return [
        ("/v1/search", search, ["POST"]),
        ("/v1/capture", capture, ["POST"]),
        ("/v1/remember", remember_endpoint, ["POST"]),
        ("/v1/objects/{object_id}", inspect, ["GET"]),
        ("/v1/objects/{object_id}/history", history, ["GET"]),
        ("/v1/objects/{object_id}/supersede", supersede, ["POST"]),
        # POST, not DELETE. The verb is part of the promise: nothing here removes a
        # row, and an API that spelled it DELETE would imply otherwise.
        ("/v1/objects/{object_id}/retire", retire, ["POST"]),
        ("/v1/compile", compile_endpoint, ["POST"]),
    ]
