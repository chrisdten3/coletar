"""Local Proxy Daemon (SCOPE §4, §10 step 1) — the wedge.

Sits in front of any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM,
llama.cpp), injects retrieved memory into the system prompt on the way in, and
extracts new memory on the way out. No export, no scraping, no ToS exposure: the
whole loop is on the user's machine.

It also doubles as the reference implementation of the connector pattern in §3.1 --
Ollama has no native MCP client, so this *is* the bridge for the local leg. Its
writes are recorded as connector writes under a `local-proxy` principal, so the
event log distinguishes "the bridge extracted this from my words" from "a frontier
model called write_memory".
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from coletar.config import get_settings
from coletar.extraction import extract_memories
from coletar.retrieval import retrieve
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import GLOBAL_SCOPE, Memory, Scope, ScopeType
from coletar.store import build_store
from coletar.store.base import Store

#: The proxy holds no bearer token -- it is a local daemon, not a remote connector --
#: so it names itself explicitly rather than writing anonymously.
PROXY_PRINCIPAL = "local-proxy"

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


async def _record(store: Store, memory: Memory, scope: Scope) -> None:
    await store.put_object(
        memory,
        event=Event(
            type=EventType.CONNECTOR_WRITE,
            object_id=memory.id,
            actor=Actor.CONNECTOR,
            provider=memory.provenance.provider,
            detail={
                "kind": memory.kind,
                "scope": str(scope),
                "principal": PROXY_PRINCIPAL,
            },
        ),
    )


async def _extract_and_store(
    store: Store, *, user_text: str, assistant_text: str, scope: Scope
) -> None:
    if not user_text.strip():
        return
    for memory in await extract_memories(
        user_text=user_text, assistant_text=assistant_text, scope=scope
    ):
        await _record(store, memory, scope)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    settings = get_settings()
    store = build_store()
    body: dict[str, Any] = await request.json()

    messages: list[dict[str, Any]] = body.get("messages", [])
    project_id = request.headers.get("x-coletar-project")
    scope: Scope = (
        Scope(type=ScopeType.PROJECT, id=project_id) if project_id else GLOBAL_SCOPE
    )

    query = _last_user_message(messages)
    if query:
        context = await retrieve(
            store,
            query,
            scope=scope,
            top_k=settings.retrieval_top_k,
            token_budget=settings.retrieval_token_budget,
        )
        body["messages"] = _inject(messages, context.as_prompt_block())

    headers = {"Content-Type": "application/json"}
    if settings.upstream_api_key:
        headers["Authorization"] = f"Bearer {settings.upstream_api_key}"

    url = f"{settings.upstream_base_url.rstrip('/')}/chat/completions"

    if body.get("stream"):

        async def upstream() -> Any:
            assembler = SSEAssembler()
            delivered_cleanly = False
            async with (
                httpx.AsyncClient(timeout=None) as client,
                client.stream("POST", url, json=body, headers=headers) as upstream_response,
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
                    store,
                    user_text=query,
                    assistant_text=assembler.reply,
                    scope=scope,
                )

        return StreamingResponse(upstream(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(url, json=body, headers=headers)

    if response.status_code != 200:
        return JSONResponse(status_code=response.status_code, content=response.json())

    payload = response.json()
    reply = payload.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    await _extract_and_store(store, user_text=query, assistant_text=reply, scope=scope)

    return JSONResponse(content=payload)


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host="127.0.0.1", port=settings.proxy_port)


if __name__ == "__main__":
    run()
