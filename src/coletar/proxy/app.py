"""Local Proxy Daemon (SCOPE §4, §10 step 1) — the wedge.

Sits in front of any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM,
llama.cpp), injects retrieved memory into the system prompt on the way in, and
extracts new memory on the way out. No export, no scraping, no ToS exposure: the
whole loop is on the user's machine.

It also doubles as the reference implementation of the connector pattern in §3.1 —
Ollama has no native MCP client, so this *is* the bridge for the local leg.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from coletar.config import get_settings
from coletar.extraction import extract_memories
from coletar.retrieval import retrieve
from coletar.schema.objects import GLOBAL_SCOPE, Scope, ScopeType
from coletar.store import build_store

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
        # Streaming path: pass bytes straight through. Extraction on streamed
        # replies needs the assembled response, so it is deferred to M2 rather
        # than silently skipped — see docs/ROADMAP.md.
        async def upstream() -> Any:
            async with (
                httpx.AsyncClient(timeout=None) as client,
                client.stream("POST", url, json=body, headers=headers) as upstream_response,
            ):
                async for chunk in upstream_response.aiter_bytes():
                    yield chunk

        return StreamingResponse(upstream(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(url, json=body, headers=headers)

    if response.status_code != 200:
        return JSONResponse(status_code=response.status_code, content=response.json())

    payload = response.json()
    reply = payload.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    for memory in await extract_memories(user_text=query, assistant_text=reply, scope=scope):
        await store.put_object(memory)

    return JSONResponse(content=payload)


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host="127.0.0.1", port=settings.proxy_port)


if __name__ == "__main__":
    run()
