"""Single-owner Vercel deployment. Account onboarding remains ROADMAP web follow-up.

The owner API and connector API have different authorities: a connector's bearer
key must never unlock the Inspector's unrestricted graph view.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from coletar.config import get_settings
from coletar.inspector.web import router, same_origin, tenant
from coletar.jobs.worker import run_pass
from coletar.mcp.server import build_app
from coletar.store import build_store
from coletar.store.postgres import PostgresStore

basic = HTTPBasic(auto_error=False)


async def owner(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)],
) -> None:
    settings = get_settings()
    if not settings.web_password:
        raise HTTPException(503, "Workspace access has not been configured.")
    if (
        credentials is None
        or not secrets.compare_digest(credentials.username.encode(), settings.web_username.encode())
        or not secrets.compare_digest(credentials.password.encode(), settings.web_password.encode())
    ):
        raise HTTPException(
            401,
            "Workspace password required.",
            headers={
                "WWW-Authenticate": 'Basic realm="coletar workspace", charset="UTF-8"',
                "Cache-Control": "no-store",
            },
        )


class ConnectionStatus(BaseModel):
    hosted: bool = True
    database: str
    mcp_url: str
    rest_url: str
    capture_enabled: bool
    upload_limit_mb: int = 4
    account_auth: bool = False
    extraction_backend: str = "off"
    worker_schedule: str = "not configured"
    credentials: str = "Connector keys are stored in the deployment's private environment file."


class BatchResult(BaseModel):
    skipped: bool
    extraction: dict[str, int]
    expiry: dict[str, int]


async def process_captures() -> BatchResult:
    settings = get_settings()
    if not settings.capture_turns or settings.extraction_provider != "openai":
        raise HTTPException(409, "Hosted extraction requires an explicit OpenAI opt-in.")
    try:
        async with asyncio.timeout(240):
            result = await run_pass(build_store(), tenant())
    except TimeoutError:
        raise HTTPException(504, "Batch timed out; pending turns can be retried.") from None
    if result.error:
        raise HTTPException(502, "Extraction failed; inspect the worker logs before retrying.")
    return BatchResult(skipped=result.skipped, extraction=result.extraction, expiry=result.expiry)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = get_settings()
        if settings.store_backend != "postgres":
            raise RuntimeError("Hosted coletar requires Postgres; function files are ephemeral.")
        if len(settings.web_password) < 24:
            raise RuntimeError("Set a random COLETAR_WEB_PASSWORD of at least 24 characters.")
        connector = build_app(stateless=True)
        app.mount("/", cast(ASGIApp, connector))
        mounted = app.router.routes[-1]
        try:
            # Mounted ASGI apps do not receive FastAPI lifespan automatically.
            transport = cast(Starlette, connector.app)
            async with transport.router.lifespan_context(transport):
                yield
        finally:
            app.router.routes.remove(mounted)
            store = build_store()
            if isinstance(store, PostgresStore):
                await store.close()

    app = FastAPI(
        title="coletar", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.middleware("http")
    async def private_responses(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/", dependencies=[Depends(owner)])
    async def home() -> RedirectResponse:
        return RedirectResponse("/app#/home")

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/web-api/connections", dependencies=[Depends(owner)], response_model=ConnectionStatus)
    async def connections() -> ConnectionStatus:
        settings = get_settings()
        await build_store().list_objects(tenant(), limit=1)
        return ConnectionStatus(
            database="connected",
            mcp_url=settings.public_url.rstrip("/") + "/mcp",
            rest_url=settings.public_url.rstrip("/") + "/v1",
            capture_enabled=settings.capture_turns,
            extraction_backend=settings.extraction_provider if settings.capture_turns else "off",
            worker_schedule="daily at 03:00 UTC, plus on demand"
            if settings.cron_secret
            else "on demand only",
        )

    @app.post("/web-api/process-captures", dependencies=[Depends(owner), Depends(same_origin)])
    async def process_now() -> BatchResult:
        return await process_captures()

    @app.get("/api/jobs/capture")
    async def scheduled_capture(request: Request) -> BatchResult:
        secret = get_settings().cron_secret
        if not secret or not secrets.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + secret
        ):
            raise HTTPException(401, "Scheduler credential required.")
        return await process_captures()

    @app.post("/v1/compile")
    async def hosted_compile() -> JSONResponse:
        # The legacy REST endpoint writes packages to a server path. That output
        # vanishes on functions; the owner's download endpoint returns the ZIP.
        return JSONResponse(
            {
                "error": "use_workspace_download",
                "message": "Build and download packages from the Migrate dashboard.",
            },
            status_code=409,
        )

    app.include_router(router, dependencies=[Depends(owner), Depends(same_origin)])
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "inspector" / "static"))
    return app
