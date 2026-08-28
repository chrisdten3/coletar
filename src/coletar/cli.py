"""coletar CLI. Thin — every command is a few lines over the same substrate.

The CLI is an *application boundary*, so it is allowed to resolve a configured
tenant — but never silently. `--tenant` is on every command that touches the graph,
writes report where they landed, and `coletar tenant` prints what the configured
default currently resolves to. A tenant that only exists in a `.env` file is implied,
not visible, and the whole point of M3.1 is that whose graph you are touching is
never a guess.
"""

from __future__ import annotations

import asyncio
import json

import typer

from coletar.config import get_settings
from coletar.retrieval import retrieve
from coletar.schema.objects import GLOBAL_SCOPE, MemoryKind, Scope, ScopeType
from coletar.schema.tenancy import TenantId
from coletar.schema.tenancy import tenant_id as parse_tenant_id
from coletar.store import build_store

TENANT_OPTION = typer.Option(
    None, "--tenant", help="Tenant to act on. Defaults to COLETAR_DEFAULT_TENANT_ID."
)


def _tenant(explicit: str | None) -> TenantId:
    """Resolve the tenant, preferring an explicit flag over configuration."""
    return parse_tenant_id(explicit or get_settings().default_tenant_id)

app = typer.Typer(help="coletar — a portable AI workspace.", no_args_is_help=True)


def _scope(project: str | None) -> Scope:
    return Scope(type=ScopeType.PROJECT, id=project) if project else GLOBAL_SCOPE


@app.command()
def serve_proxy() -> None:
    """Run the local proxy daemon in front of an OpenAI-compatible model server."""
    from coletar.proxy.app import run

    run()


@app.command()
def serve_mcp() -> None:
    """Run the hosted MCP server (streamable HTTP)."""
    from coletar.mcp.server import run

    run()


@app.command()
def remember(
    content: str,
    kind: str = typer.Option("fact", help="fact|preference|instruction|goal|correction"),
    project: str | None = typer.Option(None, help="Scope to a project id."),
    tenant: str | None = TENANT_OPTION,
) -> None:
    """Write one memory directly, bypassing any model."""
    from coletar.schema.objects import ExtractionMethod, Memory, OriginType, Provider

    async def _run() -> None:
        memory = Memory.from_write(
            content=content,
            kind=MemoryKind(kind),
            scope=_scope(project),
            provider=Provider.COLETAR,
            extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
            origin_type=OriginType.USER,
        )
        resolved = _tenant(tenant)
        await build_store().put_object(resolved, memory)
        typer.echo(f"{memory.id}  (tenant {resolved})")

    asyncio.run(_run())


@app.command()
def search(
    query: str,
    project: str | None = typer.Option(None),
    tenant: str | None = TENANT_OPTION,
) -> None:
    """Search the canonical graph the way a connected model would."""

    async def _run() -> None:
        settings = get_settings()
        resolved = _tenant(tenant)
        result = await retrieve(
            build_store(),
            resolved,
            query,
            scope=_scope(project),
            token_budget=settings.retrieval_token_budget,
            surface="cli",
        )
        typer.echo(f"tenant {resolved}")
        for obj, s in zip(result.objects, result.scores, strict=True):
            typer.echo(f"{s:.3f}  [{obj.confidence:.2f}] {obj.content}")

    asyncio.run(_run())


@app.command()
def compress(
    project: str | None = typer.Option(None), tenant: str | None = TENANT_OPTION
) -> None:
    """Run the compression job (§6) over one scope."""
    from coletar.jobs import compress as run_compress

    async def _run() -> None:
        resolved = _tenant(tenant)
        report = await run_compress(
            build_store(), resolved, scope=_scope(project) if project else None
        )
        typer.echo(json.dumps({"tenant": resolved, **report.as_dict()}, indent=2))

    asyncio.run(_run())


@app.command()
def migrate() -> None:
    """Stand the Postgres schema up from empty, or bring it up to date."""
    from coletar.store.migrate import run_migrations

    async def _run() -> None:
        applied = await run_migrations(get_settings().database_url)
        typer.echo("\n".join(applied) if applied else "already up to date")

    asyncio.run(_run())


@app.command()
def seed(tenant: str | None = TENANT_OPTION) -> None:
    """Populate the store with the fixture graph: one object of every type, plus a
    supersedes chain. Useful for trying the Inspector and the compilers."""
    from coletar.seed import seed as run_seed

    async def _run() -> None:
        resolved = _tenant(tenant)
        result = await run_seed(build_store(), resolved)
        typer.echo(f"seeded {len(result.by_role)} objects into tenant {resolved}")
        typer.echo(json.dumps(result.by_role, indent=2))

    asyncio.run(_run())


@app.command()
def history(object_id: str, tenant: str | None = TENANT_OPTION) -> None:
    """Replay one object's revisions from the event log — what a fact used to say
    and when it changed (§6). Reads the log only, never the object table."""
    from coletar.store.replay import replay_history

    async def _run() -> None:
        resolved = _tenant(tenant)
        revisions = await replay_history(build_store(), resolved, object_id)
        if not revisions:
            typer.echo(f"no revisions recorded for {object_id} in tenant {resolved}")
            return
        for revision in revisions:
            typer.echo(
                f"{revision.at.isoformat()}  v{revision.state.version}  "
                f"{revision.event.type:<18} {revision.state.content}"
            )

    asyncio.run(_run())


@app.command()
def evaluate(
    ollama: bool = typer.Option(
        False, help="Measure against Ollama instead of the hashing default."
    ),
) -> None:
    """Run the labelled retrieval evaluation and print the baseline (§5.1).

    Published alongside the numbers in docs/RETRIEVAL.md, so anyone can reproduce
    them rather than take them on trust.
    """
    from pathlib import Path

    from coletar.retrieval.embedding import HashingEmbedder, OllamaEmbedder
    from coletar.retrieval.evaluation import evaluate as run_eval
    from coletar.retrieval.evaluation import load_eval_set, seed_corpus
    from coletar.store.memory import InMemoryStore

    async def _run() -> None:
        settings = get_settings()
        embedder = (
            OllamaEmbedder(settings.upstream_base_url, settings.embedding_model,
                           settings.embedding_dim)
            if ollama
            else HashingEmbedder(settings.embedding_dim)
        )
        eval_set = load_eval_set(
            Path(__file__).parent.parent.parent / "tests" / "fixtures" / "retrieval_eval.json"
        )
        # A fixed, isolated tenant: an evaluation run must never touch real data,
        # and its numbers must not depend on what happens to be in one.
        eval_tenant = parse_tenant_id("tenant_eval")
        store = InMemoryStore(embedder=embedder)
        ids = await seed_corpus(store, eval_tenant, eval_set["corpus"])
        result = await run_eval(store, eval_tenant, eval_set, ids)
        typer.echo(f"embedder: {embedder.model}")
        typer.echo(result.report())
        for miss in result.misses:
            typer.echo(f"  miss: {miss}")

    asyncio.run(_run())


@app.command()
def tenant() -> None:
    """Show which tenant the CLI and proxy resolve to, and what the store holds.

    "Visible rather than implied" has to mean more than a setting in `.env`.
    """
    settings = get_settings()
    typer.echo(f"configured default : {settings.default_tenant_id}")
    typer.echo(f"store backend      : {settings.store_backend}")
    store = build_store()
    known = getattr(store, "tenants", None)
    if callable(known):
        found = sorted(known())
        typer.echo(f"tenants in store   : {', '.join(found) if found else '(none yet)'}")


@app.command()
def events(limit: int = 50, tenant: str | None = TENANT_OPTION) -> None:
    """Tail the Event/Revision Log — the raw feed behind the dashboard (§6)."""

    async def _run() -> None:
        resolved = _tenant(tenant)
        typer.echo(f"tenant {resolved}")
        for event in await build_store().list_events(resolved, limit=limit):
            typer.echo(
                f"{event.at.isoformat()}  {event.actor:<9} {event.type:<22} "
                f"{event.object_id or '-'}"
            )

    asyncio.run(_run())


if __name__ == "__main__":
    app()
