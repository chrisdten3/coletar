"""The hosted deployment: Clerk sign-in, one tenant per account.

**Superseded on 2026-09-18.** This module used to open the workspace to anyone with
the URL -- read *and* write, restricted objects included -- and said so at length,
deliberately, because it was a single-owner deployment. That is no longer true and
the old text is not preserved here as if it were: `/web-api` and `/app` now require
a verified Clerk session, and every route resolves its tenant from the signed-in
account rather than from `COLETAR_DEFAULT_TENANT_ID`. See
`coletar.inspector.auth`.

Registration is invite-gated (`COLETAR_INVITE_ALLOWLIST`). An empty allowlist means
closed, not open.

Two authorities are unchanged, because they never depended on the workspace gate:

  * **Connector keys gate the connector API.** A bearer key admits one surface to
    `/mcp` and `/v1`, scoped and rate-limited on its own terms.
  * **`/api/jobs` needs `CRON_SECRET`.** These are machine endpoints under an
    operator's credential, not a user's: a public trigger for a paid batch is a
    bill, and a public trigger for the migration runner is someone else's DDL.

Locality still governs what each *compiled destination* receives. It was never a
gate on the owner's own view, and it still is not -- what changed is that there is
an owner again.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from coletar.accounts import build_directory
from coletar.config import get_settings
from coletar.inspector.auth import Tenant
from coletar.inspector.web import router, same_origin
from coletar.jobs.worker import run_pass
from coletar.mcp.server import build_app
from coletar.schema.tenancy import TenantId
from coletar.schema.tenancy import tenant_id as parse_tenant_id
from coletar.store import build_store
from coletar.store.migrate import MigrationInProgress, discover, run_migrations
from coletar.store.postgres import PostgresStore

#: The health probe needs *a* tenant to shape a query and must not need a real one.
#: Nothing is ever written under it and no account can own it: it is a well-formed
#: id that exists to make one bounded SELECT run.
PROBE_TENANT = parse_tenant_id("tenant_healthz_probe")


class ConnectionStatus(BaseModel):
    hosted: bool = True
    database: str
    mcp_url: str
    rest_url: str
    capture_enabled: bool
    upload_limit_mb: int = 4
    account_auth: bool = True
    #: Surfaced so the app can say so on screen rather than leaving it to be
    #: discovered. False since accounts landed; kept as a field because the app
    #: renders a different banner for each, and a removed field would silently
    #: render the wrong one.
    public_workspace: bool = False
    extraction_backend: str = "off"
    worker_schedule: str = "not configured"
    credentials: str = "Connector keys are stored in the deployment's private environment file."


class MigrationResult(BaseModel):
    """What one run of the migration runner did.

    Both halves are reported because only the pair is an answer. `applied` alone
    cannot distinguish "nothing to do" from "it ran against the wrong database",
    and an operator checking whether a deploy is safe needs to see the schema the
    build expects, not just the delta.
    """

    applied: list[str]
    already_present: list[str]


class BatchResult(BaseModel):
    skipped: bool
    extraction: dict[str, int]
    expiry: dict[str, int]
    #: How many tenants the pass actually reached. With per-account tenants a
    #: single number for "the batch" is ambiguous; this disambiguates it.
    tenants: int = 0
    #: True when the shared time budget ran out partway. The remaining tenants are
    #: pending, not failed.
    timed_out: bool = False


async def process_captures(only: TenantId | None = None) -> BatchResult:
    """Run the extraction and expiry pass.

    `only` is the on-demand case: a signed-in person asking for their own graph to
    be processed now. With it unset this is the scheduler, and the pass runs across
    **every active account** -- which is the multi-tenant correction to a job that
    used to read `COLETAR_DEFAULT_TENANT_ID` and therefore processed exactly one
    graph no matter how many existed.

    The whole run shares one 240-second budget because the platform's request
    timeout is not per tenant. Accounts are processed in order and the budget is
    checked between them, so a timeout means "some tenants were done, the rest are
    still pending" -- which is safe: pending turns are idempotent and the next run
    picks them up. It is not safe to pretend otherwise, so the result says how far
    it got.
    """
    settings = get_settings()
    if not settings.capture_turns or settings.extraction_provider != "openai":
        raise HTTPException(409, "Hosted extraction requires an explicit OpenAI opt-in.")

    if only is not None:
        tenants = [only]
    else:
        accounts = await build_directory().list_accounts(limit=1000)
        tenants = [a.tenant_id for a in accounts if a.is_active]

    totals = BatchResult(skipped=True, extraction={}, expiry={}, tenants=0)
    store = build_store()
    try:
        async with asyncio.timeout(240):
            for owner in tenants:
                result = await run_pass(store, owner)
                if result.error:
                    raise HTTPException(
                        502, "Extraction failed; inspect the worker logs before retrying."
                    )
                totals.tenants += 1
                totals.skipped = totals.skipped and result.skipped
                for key, value in result.extraction.items():
                    totals.extraction[key] = totals.extraction.get(key, 0) + value
                for key, value in result.expiry.items():
                    totals.expiry[key] = totals.expiry.get(key, 0) + value
    except TimeoutError:
        # Not an error: the remaining tenants are still pending and the next run
        # takes them. Reporting a 504 here would make a partially-successful,
        # perfectly recoverable pass look like an outage.
        totals.timed_out = True
    return totals


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = get_settings()
        if settings.store_backend != "postgres":
            raise RuntimeError("Hosted coletar requires Postgres; function files are ephemeral.")
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

    @app.get("/")
    async def home() -> RedirectResponse:
        return RedirectResponse("/app#/home")

    @app.get("/healthz")
    async def health() -> JSONResponse:
        """Liveness *and* readiness.

        This used to return `ok` without touching anything, which made it a check
        that the Python process had started -- true even with the database
        unreachable, which is the only interesting way this deployment fails. One
        bounded query against the store is what makes the probe mean something.
        """
        try:
            async with asyncio.timeout(5):
                await build_store().list_objects(PROBE_TENANT, limit=1)
        except Exception:
            # No detail in the body: this endpoint is unauthenticated by necessity
            # (a probe cannot carry a credential) and a connection string in an
            # error is how one ends up in someone's uptime dashboard.
            return JSONResponse({"status": "degraded", "database": "unreachable"}, 503)
        return JSONResponse({"status": "ok", "database": "connected"})

    @app.get("/web-api/connections", response_model=ConnectionStatus)
    async def connections(owner: Tenant) -> ConnectionStatus:
        settings = get_settings()
        await build_store().list_objects(owner, limit=1)
        return ConnectionStatus(
            database="connected",
            mcp_url=settings.public_url.rstrip("/") + "/mcp",
            rest_url=settings.public_url.rstrip("/") + "/v1",
            capture_enabled=settings.capture_turns,
            extraction_backend=settings.extraction_provider if settings.capture_turns else "off",
            worker_schedule="daily at 03:00 UTC, plus on demand"
            if settings.cron_secret
            else "on demand only",
            account_auth=True,
            public_workspace=False,
        )

    @app.post("/web-api/process-captures", dependencies=[Depends(same_origin)])
    async def process_now(owner: Tenant) -> BatchResult:
        # Scoped to the caller's own graph. The unscoped form is the scheduler's,
        # and a signed-in person must not be able to spend the whole deployment's
        # extraction budget by pressing a button on their own settings page.
        return await process_captures(only=owner)

    def operator_only(request: Request) -> None:
        """`CRON_SECRET`, or nothing. Shared by both `/api/jobs` endpoints.

        Fails closed when the variable is unset: a deployment that never
        configured a scheduler credential has not thereby opened its batch job
        and its migration runner to the internet.
        """
        secret = get_settings().cron_secret
        if not secret or not secrets.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + secret
        ):
            raise HTTPException(401, "Scheduler credential required.")

    @app.get("/api/jobs/capture")
    async def scheduled_capture(request: Request) -> BatchResult:
        operator_only(request)
        return await process_captures()

    @app.post("/api/jobs/migrate")
    async def apply_migrations(request: Request) -> MigrationResult:
        """Apply pending schema migrations to this deployment's database.

        Here because this platform has no release command. A container host runs
        `coletar migrate` before a new version takes traffic (`fly.toml`); Vercel
        has no equivalent hook, and the app deliberately does not run DDL from a
        cold start, where a dozen instances would race each other on every deploy.
        What was left was a manual step against the production DSN from somebody's
        laptop -- which is exactly the step that got skipped when migration 014
        shipped, and `/web-api/pricing` answered 500 until someone noticed.

        Three things keep an endpoint that runs DDL defensible:

          * **It is the operator's credential, not a user's.** `CRON_SECRET` is
            deployment configuration, the same authority that can already spend
            the extraction budget. No workspace session reaches this.
          * **It applies what is already in the build.** The migrations are the
            ones in this deployment's own `migrations/` directory, checksummed
            against the ledger; it takes no SQL from the request, and an edited
            migration that has already been applied is refused rather than
            re-run.
          * **POST, so a schedule cannot reach it.** Vercel's cron issues GET.
            Migrations are a thing an operator decides to do, once, next to a
            deploy -- not something that quietly happens at 03:00.
        """
        operator_only(request)
        try:
            # Bounded like the batch pass, and for the same reason: the platform
            # will kill a long function anyway, and a 504 that says so beats a
            # platform timeout page. Each migration commits on its own, so an
            # interrupted run leaves the ledger honest about how far it got and
            # the next call picks up from there.
            async with asyncio.timeout(240):
                applied = await run_migrations(get_settings().database_url)
        except MigrationInProgress as error:
            raise HTTPException(409, str(error)) from error
        except TimeoutError as error:
            raise HTTPException(
                504,
                "Migrations did not finish within the request budget. The ledger "
                "records what was applied; call again to continue.",
            ) from error
        except RuntimeError as error:
            # The runner's own refusals -- chiefly a migration edited after it was
            # applied. The person holding this credential is the person who can
            # fix that, so they get the sentence rather than a blank 500.
            raise HTTPException(500, str(error)) from error
        return MigrationResult(
            applied=applied,
            already_present=[m.filename for m in discover() if m.filename not in applied],
        )

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

    # `same_origin` is not authentication. It stops another site's page from
    # POSTing here in a visitor's browser; it does not stop anyone who asks
    # directly, and with the owner check gone nothing does.
    app.include_router(router, dependencies=[Depends(same_origin)])
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "inspector" / "static"))
    return app
