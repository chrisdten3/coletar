"""Local Proxy Daemon (SCOPE §4, §10 step 1) — the wedge.

Sits in front of any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM,
llama.cpp), injects retrieved memory into the system prompt, and applies the chosen
post-response capture policy. Passive writes are off by default. No export, no
scraping, no ToS exposure: the whole loop is on the user's machine.

It also doubles as the reference implementation of the connector pattern in §3.1 --
Ollama has no native MCP client, so this *is* the bridge for the local leg. Its
writes are recorded as connector writes under a named principal, so the event log
distinguishes "the bridge extracted this from my words" from "a frontier model
called write_memory".

M4.2: it no longer opens the database. Everything it needs from the graph goes
through a `ContextClient` carrying an explicit principal -- in-process by default so
the wedge still runs with no infrastructure, or as an MCP client holding no database
credentials when a server is configured. See `coletar.proxy.client`.
"""

from __future__ import annotations

import json
import warnings
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from coletar.config import get_settings
from coletar.extraction import extract_memories
from coletar.proxy.client import ContextClient, build_context_client
from coletar.schema.objects import GLOBAL_SCOPE, Memory, Scope, ScopeType

#: Built once per process. The client owns the principal, so which graph the daemon
#: writes into is decided at startup and printed there, never per request.
_client: ContextClient | None = None


def context_client() -> ContextClient:
    global _client
    if _client is None:
        _client = build_context_client()
    return _client


def reset_client() -> None:
    """Drop the process-wide client. Tests need this; a running server does not."""
    global _client
    _client = None


app = FastAPI(title="coletar local proxy", version="0.1.0")


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content", "")
            if isinstance(content, str):
                return content
            # Multimodal content blocks: keep the text parts.
            return " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
    return ""


def _inject(messages: list[dict[str, Any]], block: str) -> list[dict[str, Any]]:
    """Prepend to an existing system message rather than adding a second one —
    several local runtimes only honour the first."""
    if not block:
        return messages
    out = list(messages)
    if out and out[0].get("role") == "system":
        out[0] = {**out[0], "content": f"{block}\n\n{out[0].get('content', '')}".strip()}
    else:
        out.insert(0, {"role": "system", "content": block})
    return out


class SSEAssembler:
    """Reassembles an OpenAI-compatible streamed completion as it passes through.

    Chunk boundaries fall wherever the network puts them, so a `data:` line can
    arrive split across two reads; the tail is held back until its newline shows up.
    Nothing here can raise on malformed input: a proxy that breaks someone's chat
    because it could not parse a telemetry frame is worse than one that learns
    nothing from it.
    """

    def __init__(self) -> None:
        self._pending = ""
        self._parts: list[str] = []

    @property
    def reply(self) -> str:
        return "".join(self._parts)

    def feed(self, chunk: bytes) -> None:
        self._pending += chunk.decode("utf-8", errors="replace")
        *lines, self._pending = self._pending.split("\n")
        for line in lines:
            self._consume(line.strip())

    def close(self) -> None:
        if self._pending.strip():
            self._consume(self._pending.strip())
            self._pending = ""

    def _consume(self, line: str) -> None:
        if not line.startswith("data:"):
            return
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            return
        try:
            frame = json.loads(payload)
            for choice in frame.get("choices", []):
                # Streamed completions carry `delta`; some runtimes emit a final
                # non-delta `message` frame as well.
                piece = (choice.get("delta") or choice.get("message") or {}).get("content")
                if isinstance(piece, str):
                    self._parts.append(piece)
        except (ValueError, AttributeError):
            return


async def _record(client: ContextClient, memory: Memory, scope: Scope) -> None:
    # Through the client, which goes through the ingest boundary, so a preference the
    # user restates in a later conversation corroborates the object that already says
    # it rather than growing the graph a copy the compiler would later emit twice.
    await client.write(memory, scope=scope)


async def _extract_and_store(
    client: ContextClient, *, user_text: str, assistant_text: str, scope: Scope
) -> None:
    """Apply the configured live policy after delivery — never in front of it.

    The compatibility heuristic is fast; encrypted collection is also only a local
    write. Semantic extraction is deliberately a separate worker, and a user never
    waits on the proxy learning something to receive the answer they asked for.

    Nothing raised here may reach the user, for the same reason: the reply already
    left. A proxy that fails a chat because it could not extract a memory is worse
    than one that quietly learns nothing from that turn.
    """
    if not user_text.strip():
        return
    try:
        settings = get_settings()
        if settings.live_extraction_mode == "off":
            return
        if settings.live_extraction_mode == "collect_then_batch":
            if not settings.capture_turns:
                raise RuntimeError(
                    "collect_then_batch requires COLETAR_CAPTURE_TURNS=true"
                )
            await client.capture(user_text, scope=scope)
            return
        for memory in await extract_memories(
            user_text=user_text, assistant_text=assistant_text, scope=scope
        ):
            await _record(client, memory, scope)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        warnings.warn(f"coletar: extraction failed for this turn: {exc!r}", stacklevel=2)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, background: BackgroundTasks) -> Any:
    settings = get_settings()
    client = context_client()
    body: dict[str, Any] = await request.json()

    messages: list[dict[str, Any]] = body.get("messages", [])
    project_id = request.headers.get("x-coletar-project")
    scope: Scope = (
        Scope(type=ScopeType.PROJECT, id=project_id) if project_id else GLOBAL_SCOPE
    )

    query = _last_user_message(messages)
    if query:
        # Scope, locality and the retrieval trace are all the client's business
        # now: it holds the principal, and the principal is what decides them.
        body["messages"] = _inject(messages, await client.context_block(query, scope=scope))

    headers = {"Content-Type": "application/json"}
    if settings.upstream_api_key:
        headers["Authorization"] = f"Bearer {settings.upstream_api_key}"

    url = f"{settings.upstream_base_url.rstrip('/')}/chat/completions"

    if body.get("stream"):

        async def upstream() -> Any:
            assembler = SSEAssembler()
            delivered_cleanly = False
            async with (
                httpx.AsyncClient(timeout=None) as http,
                http.stream("POST", url, json=body, headers=headers) as upstream_response,
            ):
                async for chunk in upstream_response.aiter_bytes():
                    # Forward first, parse second. Reassembly must never sit between
                    # the model and the user's screen -- streaming exists so the
                    # first token arrives immediately.
                    yield chunk
                    assembler.feed(chunk)
                delivered_cleanly = upstream_response.status_code == 200
            assembler.close()
            # Extraction happens only after a complete, successful exchange: a turn
            # the model never answered should not teach us anything.
            if delivered_cleanly:
                await _extract_and_store(
                    client,
                    user_text=query,
                    assistant_text=assembler.reply,
                    scope=scope,
                )

        return StreamingResponse(upstream(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=300.0) as http:
        response = await http.post(url, json=body, headers=headers)

    if response.status_code != 200:
        return JSONResponse(status_code=response.status_code, content=response.json())

    payload = response.json()
    reply = payload.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    # Queued, not awaited: the response goes out first and extraction happens behind
    # it. The streaming path below already has this property, because its extraction
    # runs once the stream has finished being forwarded.
    background.add_task(
        _extract_and_store, client, user_text=query, assistant_text=reply, scope=scope
    )

    return JSONResponse(content=payload)


def run() -> None:
    import uvicorn

    settings = get_settings()
    # Visible rather than implied: the operator should not have to read .env to
    # find out whose graph this daemon is about to write into.
    print(f"coletar proxy -> {context_client().label}")
    print(f"                upstream {settings.upstream_base_url}")
    uvicorn.run(app, host="127.0.0.1", port=settings.proxy_port)


if __name__ == "__main__":
    run()
