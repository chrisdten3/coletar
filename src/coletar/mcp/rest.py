"""A minimal REST surface for the browser bridge (SCOPE §9, M3.6).

The MCP server is the right interface for a model. It is the wrong one for a browser
extension: MCP is JSON-RPC over a streamable-HTTP session, and a content script wants
two POSTs. So the same store, the same auth and the same ingest path are exposed here
as two endpoints — this is a slice of the REST surface §9 owes anyway, brought
forward because the composer bridge needs it.

**These endpoints are the whole API the extension gets.** It can retrieve context and
it can record consented turns, with assistant replies kept as evidence only. There is
deliberately no endpoint for enumerating stored conversations or graph objects;
provider-page observation stays in the explicitly consented, active-page extension.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from coletar.config import get_settings
from coletar.decisions import consent, raw_capture
from coletar.decisions import promotion as promotion_gate
from coletar.decisions.backend import DecisionQuery, DecisionResult, build_decision_backend
from coletar.decisions.consent import RAW_CAPTURE, VENDOR_SEND
from coletar.decisions.promotion import PROMOTION_MIN_OCCURRENCES
from coletar.history.patterns import find_repeat_decisions
from coletar.ingest import remember
from coletar.mcp.auth import (
    SCOPE_CONSENT,
    SCOPE_READ,
    SCOPE_WRITE,
    Principal,
    current_principal,
)
from coletar.mcp.schemas import ObjectView
from coletar.retrieval import retrieve
from coletar.retrieval.context import INJECTION_MARKER
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    GLOBAL_SCOPE,
    ExtractionMethod,
    Memory,
    MemoryKind,
    ObjectType,
    OriginType,
    Provider,
    Scope,
    ScopeType,
    Sensitivity,
)
from coletar.store import build_store

logger = logging.getLogger(__name__)

MAX_QUERY_CHARS = 4_000
MAX_CONTENT_CHARS = 4_000
#: A signature is a short label, not a sentence -- this catches a caller pasting
#: real decision content in by mistake.
MAX_SIGNATURE_CHARS = 200
#: Raw capture holds real strings rather than labels, so the bound is far higher --
#: but still a bound. An unbounded field is an invitation to post an archive.
MAX_RAW_FIELD_CHARS = 20_000
#: A ceiling on the purge scan a consent revocation triggers.
SCAN_LIMIT = 10_000


class SearchRequest(BaseModel):
    query: str
    project_id: str | None = None
    top_k: int = Field(default=6, ge=1, le=25)
    #: "terse" for a composer a person will read, "full" for a model's system prompt.
    style: str = "full"
    #: Which surface asked, for the trace. Not trusted for anything but reporting.
    surface: str = "bridge"


class CaptureRequest(BaseModel):
    """A consented browser turn; assistant turns are retained as evidence only."""

    text: str
    role: Literal["user", "assistant"] = "user"
    turn_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[\w-]+$")
    conversation_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[\w-]+$"
    )
    project_id: str | None = None
    surface: str = "bridge"


class RememberRequest(BaseModel):
    content: str
    kind: MemoryKind = MemoryKind.FACT
    project_id: str | None = None
    surface: str = "bridge"


class ConsentRequest(BaseModel):
    """Grant one consent, for one tool, deliberately.

    `confirm` must be literally true. It is friction on purpose: a consent that a
    copy-pasted config blob can grant by omission is not a consent.
    """

    tool_name: str
    consent_type: Literal["raw_capture", "vendor_send"]
    sensitivity: Sensitivity = Sensitivity.NORMAL
    confirm: bool = False


class ConsentRevokeRequest(BaseModel):
    tool_name: str
    consent_type: Literal["raw_capture", "vendor_send"]


class RawTraceRequest(BaseModel):
    """What one of the caller's own tools was asked, and what it answered.

    Unlike `DecisionRequest` these are the real strings, which is why this path is
    refused unless the tenant has opted this specific tool in. Nothing here is ever
    parsed as an instruction -- see `coletar.decisions.raw_capture`.
    """

    tool_name: str
    intent: str
    environmental_state: dict[str, Any] = Field(default_factory=dict)
    tool_call: dict[str, Any] = Field(default_factory=dict)
    tool_response: dict[str, Any] = Field(default_factory=dict)


class PromoteRequest(BaseModel):
    tool_name: str
    input_signature: str
    #: The caller's own attestation about what this tool's decision does. A tool that
    #: gates whether something consequential may proceed is refused rather than held
    #: to a higher bar -- see `coletar.decisions.promotion`.
    safety_gating: bool = False


class DemoteRequest(BaseModel):
    tool_name: str
    input_signature: str
    reason: str = "manual"


class ResolveRequest(BaseModel):
    tool_name: str
    input_signature: str
    candidate_outcomes: list[str] | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class DecisionRequest(BaseModel):
    """One reported outcome of a tool a caller's own code just ran.

    `input_signature` and `outcome_signature` are labels the caller chooses --
    "route:enterprise-billing", "approve-type-a" -- never the real decision
    content. coletar cannot know what a decision means across every business it
    serves and does not try to; it only checks whether the same labelled outcome
    keeps recurring for the same labelled situation (`history.patterns`).
    """

    tool_name: str
    input_signature: str
    outcome_signature: str


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
    """Offer a turn the user typed to the configured passive-capture policy.

    This is the difference between capture and `/v1/remember`. Remember stores what it
    is given because the user asked explicitly. Passive inference is off by default.
    Collect-then-batch stores encrypted working material and makes no memory claim;
    the legacy heuristic must be selected explicitly.

    Consented browser replies are encrypted evidence with agent provenance, never
    inputs to the user-fact extractor. Identified turns are idempotent on retry.
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
    if body.role == "user" and INJECTION_MARKER in text:
        text = text.split(INJECTION_MARKER)[-1].strip()
    if not text:
        return JSONResponse({"error": "bad_request", "message": "text is empty"}, 400)
    if len(text) > (100_000 if body.turn_id else MAX_CONTENT_CHARS):
        return JSONResponse({"error": "bad_request", "message": "text too long"}, 400)

    from coletar.capture import CaptureBusy, CaptureConflict, capture_turn, is_pending
    from coletar.config import get_settings
    from coletar.extraction import extract_memories

    scope = _scope(body.project_id)
    store = build_store()

    # Kept before anything judges it, and kept whether or not the heuristic finds
    # something — a turn the heuristic missed is exactly what the batch pass is for.
    # Off by default: storing verbatim turns is a larger commitment than storing
    # extracted memories and should be a decision, not a discovery.
    settings = get_settings()
    episode = None
    if body.role == "assistant" and not body.turn_id:
        return JSONResponse({"error": "bad_request", "message": "assistant needs turn_id"}, 400)
    if body.turn_id and not settings.capture_turns:
        return JSONResponse({"error": "capture_not_enabled", "capture_enabled": False}, 503)
    if settings.capture_turns:
        try:
            episode = await capture_turn(
                store,
                principal.tenant_id,
                text,
                surface=surface,
                scope=scope,
                principal_id=principal.id,
                detail={"surface": body.surface},
                role=body.role,
                turn_id=body.turn_id,
                conversation_id=body.conversation_id,
            )
        except CaptureBusy as exc:
            return JSONResponse({"error": "capture_busy", "message": str(exc)}, 409)
        except CaptureConflict as exc:
            return JSONResponse({"error": "capture_conflict", "message": str(exc)}, 409)
    # Identified browser turns use encrypted retention only. Retrying must not run
    # legacy extraction twice; model output must never enter user-fact extraction.
    if episode is not None and body.turn_id:
        return JSONResponse(
            {
                "extracted": [],
                "count": 0,
                "queued": is_pending(episode),
                "episode_id": episode.id,
                "capture_enabled": True,
                "stored": True,
            }
        )

    # Safe default: passive composer observation makes no inferred graph write.
    # Explicit `/v1/remember` and MCP `write_memory` continue to work. If capture was
    # independently enabled, report the episode as queued for an out-of-band pass.
    if settings.live_extraction_mode == "off":
        response: dict[str, Any] = {
            "extracted": [],
            "count": 0,
            "queued": episode is not None,
            # Tells a client the difference between "nothing in that turn was worth
            # keeping" and "nothing here is switched on". Both used to be a bare
            # count of zero, so the extension's own capture toggle could be enabled
            # against a server that would never act on it, and the only symptom was
            # silence on every send. A client cannot warn about a state it cannot
            # observe.
            "capture_enabled": settings.capture_turns,
        }
        if episode is not None:
            response["episode_id"] = episode.id
        return JSONResponse(response)

    # Collect-then-batch deliberately writes no regex memory. The live benchmark is
    # a narrow regression set, not enough evidence to expose a preliminary guess to
    # retrieval before the semantic pass. Capture must be explicitly enabled or the
    # mode would acknowledge turns that were stored nowhere.
    if settings.live_extraction_mode == "collect_then_batch":
        if episode is None:
            return JSONResponse(
                {
                    "error": "capture_not_enabled",
                    "message": "collect_then_batch requires COLETAR_CAPTURE_TURNS=true",
                },
                status_code=503,
            )
        return JSONResponse({"extracted": [], "count": 0, "queued": True, "episode_id": episode.id})

    stored: list[dict[str, Any]] = []
    for memory in await extract_memories(user_text=text, scope=scope):
        if episode is not None:
            # Points at the turn in the graph, not at an external id. This is what
            # lights up the Inspector's episode lineage: click a memory, see the
            # sentence it came from, in the user's own words.
            memory.provenance.source_object_ids = [episode.id]
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
            {
                "id": result.object_id,
                "content": memory.content,
                "kind": memory.kind,
                "created": result.created,
            }
        )
    return JSONResponse({"extracted": stored, "count": len(stored)})


async def report_decision(request: Request) -> JSONResponse:
    """Record what a caller's own tool call decided, for pattern analysis.

    Not part of the browser bridge -- this is for a developer's own backend,
    called directly from inside their tool code the way a Datadog client is,
    right after the tool ran. No Origin check, because there is no browser here.
    """
    principal = _require(SCOPE_WRITE)
    if isinstance(principal, JSONResponse):
        return principal
    try:
        body = DecisionRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)

    for field_name, value in (
        ("tool_name", body.tool_name),
        ("input_signature", body.input_signature),
        ("outcome_signature", body.outcome_signature),
    ):
        if not value.strip():
            return JSONResponse(
                {"error": "bad_request", "message": f"{field_name} must be non-empty"}, 400
            )
        if len(value) > MAX_SIGNATURE_CHARS:
            return JSONResponse(
                {
                    "error": "bad_request",
                    "message": (
                        f"{field_name} is {len(value)} characters; the limit is "
                        f"{MAX_SIGNATURE_CHARS}. Pass a short label, not the decision itself."
                    ),
                },
                400,
            )

    await build_store().append_event(
        principal.tenant_id,
        Event(
            type=EventType.DECISION_OBSERVED,
            actor=Actor.CONNECTOR,
            detail={
                "tool_name": body.tool_name,
                "input_signature": body.input_signature,
                "outcome_signature": body.outcome_signature,
                "principal": principal.id,
            },
        ),
    )
    return JSONResponse({"recorded": True})


# --- M11: raw decision capture, consent, and gated automated resolution ----------
#
# Four properties hold across everything below, and each is enforced rather than
# documented:
#
#   * Raw capture is refused unless the tenant opted *that tool* in.
#   * Granting a consent or a promotion needs `SCOPE_CONSENT`; using one needs only
#     `write`. The key an integration reports decisions with cannot widen what is
#     captured about them, or put a pattern on autopilot.
#   * `/v1/decisions/resolve` checks the promotion record before it reaches a
#     backend, so an unapproved pattern never leaves this process.
#   * A backend that cannot answer confidently produces `decided: false`, never an
#     exception and never a guess.


async def grant_consent(request: Request) -> JSONResponse:
    """Record a tenant's consent for one tool. Requires the `consent` scope."""
    principal = _require(SCOPE_CONSENT)
    if isinstance(principal, JSONResponse):
        return principal
    try:
        body = ConsentRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)
    if not body.tool_name.strip():
        return JSONResponse({"error": "bad_request", "message": "tool_name is empty"}, 400)
    if not body.confirm:
        return JSONResponse(
            {
                "error": "confirmation_required",
                "message": (
                    "pass confirm=true to grant this consent; it permits coletar to "
                    "store this tool's real inputs and outputs"
                ),
            },
            400,
        )

    record = await consent.grant(
        build_store(),
        principal.tenant_id,
        body.tool_name.strip(),
        body.consent_type,
        sensitivity=body.sensitivity,
        principal_id=principal.id,
    )
    return JSONResponse({"granted": True, "consent": record})


async def revoke_consent(request: Request) -> JSONResponse:
    """Withdraw a consent and shred whatever raw content it covered.

    Revocation purges rather than merely stopping new captures: leaving a day of
    already-captured content sitting there would make "we stopped" the answer to a
    question nobody asked.
    """
    principal = _require(SCOPE_CONSENT)
    if isinstance(principal, JSONResponse):
        return principal
    try:
        body = ConsentRevokeRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)

    store = build_store()
    tool_name = body.tool_name.strip()
    await consent.revoke(
        store, principal.tenant_id, tool_name, body.consent_type, principal_id=principal.id
    )

    purged = 0
    if body.consent_type == RAW_CAPTURE:
        episodes = await store.list_objects(
            principal.tenant_id, type=ObjectType.EPISODE, limit=SCAN_LIMIT
        )
        for episode in episodes:
            if (
                episode.payload.get(raw_capture.EPISODE_KIND) == raw_capture.DECISION_RAW
                and episode.payload.get("tool_name") == tool_name
                and await store.shred_object_key(
                    principal.tenant_id, episode.id, reason="consent_revoked"
                )
            ):
                purged += 1
    return JSONResponse({"revoked": True, "purged_raw_traces": purged})


async def capture_raw_trace(request: Request) -> JSONResponse:
    """Accept one tool call's real inputs and outputs, if this tool is opted in."""
    principal = _require(SCOPE_WRITE)
    if isinstance(principal, JSONResponse):
        return principal
    try:
        body = RawTraceRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)

    tool_name = body.tool_name.strip()
    if not tool_name:
        return JSONResponse({"error": "bad_request", "message": "tool_name is empty"}, 400)
    for field_name, value in (("intent", body.intent),):
        if len(value) > MAX_RAW_FIELD_CHARS:
            return JSONResponse(
                {
                    "error": "bad_request",
                    "message": f"{field_name} exceeds {MAX_RAW_FIELD_CHARS} characters",
                },
                400,
            )
    for field_name, blob in (
        ("environmental_state", body.environmental_state),
        ("tool_call", body.tool_call),
        ("tool_response", body.tool_response),
    ):
        if len(json.dumps(blob)) > MAX_RAW_FIELD_CHARS:
            return JSONResponse(
                {
                    "error": "bad_request",
                    "message": f"{field_name} exceeds {MAX_RAW_FIELD_CHARS} serialized characters",
                },
                400,
            )

    store = build_store()
    if not await consent.is_consented(
        store, principal.tenant_id, tool_name, RAW_CAPTURE
    ):
        return JSONResponse(
            {
                "error": "raw_capture_not_consented",
                "message": (
                    f"raw capture is not enabled for tool {tool_name!r}; grant it via "
                    "POST /v1/decisions/consent with a consent-scoped key"
                ),
            },
            403,
        )

    episode = await raw_capture.capture_trace(
        store,
        principal.tenant_id,
        tool_name=tool_name,
        intent=body.intent,
        environmental_state=body.environmental_state,
        tool_call=body.tool_call,
        tool_response=body.tool_response,
        surface=principal.surface,
        scope=GLOBAL_SCOPE,
        principal_id=principal.id,
    )
    return JSONResponse({"captured": True, "trace_id": episode.id})


async def promote_pattern(request: Request) -> JSONResponse:
    """Put one (tool, situation) pattern on autopilot. Requires the `consent` scope.

    Refuses anything the caller attests is a safety gate, and anything whose evidence
    is thinner than `PROMOTION_MIN_OCCURRENCES` identical outcomes. Nothing promotes
    itself: this endpoint is the only way a pattern reaches automated resolution.
    """
    principal = _require(SCOPE_CONSENT)
    if isinstance(principal, JSONResponse):
        return principal
    try:
        body = PromoteRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)

    if body.safety_gating:
        return JSONResponse(
            {
                "error": "safety_gating_not_promotable",
                "message": (
                    "a safety-gating decision cannot be automated in this build. "
                    "Capture and analysis continue; resolution stays advisory."
                ),
            },
            400,
        )

    store = build_store()
    patterns = await find_repeat_decisions(store, principal.tenant_id)
    match = next(
        (
            p
            for p in patterns
            if p.tool_name == body.tool_name and p.input_signature == body.input_signature
        ),
        None,
    )
    if match is None:
        return JSONResponse(
            {
                "error": "no_evidence",
                "message": (
                    "no repeat decisions recorded for that tool and situation; "
                    "report outcomes via POST /v1/decisions first"
                ),
            },
            400,
        )
    if not match.consistent or match.occurrences < PROMOTION_MIN_OCCURRENCES:
        return JSONResponse(
            {
                "error": "insufficient_evidence",
                "message": (
                    f"needs {PROMOTION_MIN_OCCURRENCES} consistent occurrences; "
                    f"have {match.occurrences} (consistent={match.consistent})"
                ),
                "evidence": {
                    "occurrences": match.occurrences,
                    "consistent": match.consistent,
                    "required": PROMOTION_MIN_OCCURRENCES,
                },
            },
            400,
        )

    record = await promotion_gate.promote(
        store,
        principal.tenant_id,
        body.tool_name,
        body.input_signature,
        evidence={
            "occurrences": match.occurrences,
            "consistent": match.consistent,
            "last_outcome": match.last_outcome,
        },
        principal_id=principal.id,
    )
    return JSONResponse({"promoted": True, "promotion": record})


async def demote_pattern(request: Request) -> JSONResponse:
    """Take a pattern off autopilot. The kill switch; takes effect immediately."""
    principal = _require(SCOPE_CONSENT)
    if isinstance(principal, JSONResponse):
        return principal
    try:
        body = DemoteRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)

    demoted = await promotion_gate.demote(
        build_store(),
        principal.tenant_id,
        body.tool_name,
        body.input_signature,
        reason=body.reason,
        principal_id=principal.id,
    )
    return JSONResponse({"demoted": demoted})


async def resolve_decision(request: Request) -> JSONResponse:
    """Answer a promoted pattern from a decision backend, or hand the call back.

    Always 200. `decided: false` is the normal, expected answer -- for an unapproved
    pattern, an unavailable backend, or an answer that did not clear its confidence
    floor -- and the caller's branch for it is "use your model instead". An exception
    here would be something an integrator could catch and read as permission.
    """
    principal = _require(SCOPE_WRITE)
    if isinstance(principal, JSONResponse):
        return principal
    try:
        body = ResolveRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)

    store = build_store()
    approved = await promotion_gate.get_promotion(
        store, principal.tenant_id, body.tool_name, body.input_signature
    )
    if approved is None:
        # The gate: no vendor is consulted for a pattern no human approved.
        return JSONResponse(
            DecisionResult(decided=False, reason="not_promoted").model_dump(mode="json")
        )

    query = DecisionQuery(
        tool_name=body.tool_name,
        input_signature=body.input_signature,
        candidate_outcomes=body.candidate_outcomes,
        # Raw context only travels to a vendor the tenant named for this tool.
        context=(
            body.context
            if await consent.is_consented(
                store, principal.tenant_id, body.tool_name, VENDOR_SEND
            )
            else {}
        ),
    )
    try:
        backend = await build_decision_backend(store, principal.tenant_id, body.tool_name)
        result = await backend.decide(query)
    except Exception as exc:  # noqa: BLE001 - every failure is a fallback, not a 500
        logger.warning("decision backend unavailable: %r", exc)
        return JSONResponse(
            DecisionResult(decided=False, reason="backend_unavailable").model_dump(mode="json")
        )

    if result.decided and result.outcome_signature:
        # Only real automated decisions earn an event. Routine fallbacks are an
        # observability concern; logging one per call would bury the log in
        # non-events, the way per-hit retrieval rows once did.
        await store.append_event(
            principal.tenant_id,
            Event(
                type=EventType.DECISION_OBSERVED,
                actor=Actor.CONNECTOR,
                detail={
                    "tool_name": body.tool_name,
                    "input_signature": body.input_signature,
                    "outcome_signature": result.outcome_signature,
                    "resolved_by": result.backend,
                    "confidence": result.confidence,
                    "promotion_approved_at": approved.get("approved_at"),
                    "principal": principal.id,
                },
            ),
        )
    return JSONResponse(result.model_dump(mode="json"))


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
        ("/v1/decisions", report_decision, ["POST"]),
        # M11. Granting needs the `consent` scope; using what was granted does not.
        ("/v1/decisions/consent", grant_consent, ["POST"]),
        ("/v1/decisions/consent/revoke", revoke_consent, ["POST"]),
        ("/v1/decisions/raw", capture_raw_trace, ["POST"]),
        ("/v1/decisions/promote", promote_pattern, ["POST"]),
        ("/v1/decisions/demote", demote_pattern, ["POST"]),
        ("/v1/decisions/resolve", resolve_decision, ["POST"]),
        ("/v1/objects/{object_id}", inspect, ["GET"]),
        ("/v1/objects/{object_id}/history", history, ["GET"]),
        ("/v1/objects/{object_id}/supersede", supersede, ["POST"]),
        # POST, not DELETE. The verb is part of the promise: nothing here removes a
        # row, and an API that spelled it DELETE would imply otherwise.
        ("/v1/objects/{object_id}/retire", retire, ["POST"]),
        ("/v1/compile", compile_endpoint, ["POST"]),
    ]
