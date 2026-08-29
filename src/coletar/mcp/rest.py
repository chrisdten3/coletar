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
    try:
        body = SearchRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001 - any malformed body is a 400
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)
    if not body.query.strip():
        return JSONResponse({"error": "bad_request", "message": "query is empty"}, 400)
    if len(body.query) > MAX_QUERY_CHARS:
        return JSONResponse({"error": "bad_request", "message": "query too long"}, 400)

    settings = get_settings()
    result = await retrieve(
        build_store(),
        principal.tenant_id,
        body.query,
        scope=_scope(body.project_id),
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
            "prompt_block": result.as_prompt_block(),
            "token_estimate": result.token_estimate,
        }
    )


async def remember_endpoint(request: Request) -> JSONResponse:
    """Record something the user typed. Never something a model produced."""
    principal = _require(SCOPE_WRITE)
    if isinstance(principal, JSONResponse):
        return principal
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
        provider=Provider.COLETAR,
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
    )
    return JSONResponse({"id": result.object_id, "stored": result.created})


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
    try:
        body = CaptureRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)
    text = body.text.strip()
    if not text:
        return JSONResponse({"error": "bad_request", "message": "text is empty"}, 400)
    if len(text) > MAX_CONTENT_CHARS:
        return JSONResponse({"error": "bad_request", "message": "text too long"}, 400)

    from coletar.extraction import extract_memories

    scope = _scope(body.project_id)
    store = build_store()
    stored: list[dict[str, Any]] = []
    for memory in await extract_memories(user_text=text, scope=scope):
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
        )
        stored.append(
            {"id": result.object_id, "content": memory.content,
             "kind": memory.kind, "created": result.created}
        )
    return JSONResponse({"extracted": stored, "count": len(stored)})


def routes() -> list[tuple[str, Any, list[str]]]:
    return [
        ("/v1/search", search, ["POST"]),
        ("/v1/capture", capture, ["POST"]),
        ("/v1/remember", remember_endpoint, ["POST"]),
    ]
