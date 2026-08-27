"""coletar CLI. Thin — every command is a few lines over the same substrate."""

from __future__ import annotations

import asyncio
import json

import typer

from coletar.config import get_settings
from coletar.retrieval import retrieve
from coletar.schema.objects import GLOBAL_SCOPE, MemoryKind, Scope, ScopeType
from coletar.store import build_store

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
        await build_store().put_object(memory)
        typer.echo(memory.id)

    asyncio.run(_run())


@app.command()
def search(query: str, project: str | None = typer.Option(None)) -> None:
    """Search the canonical graph the way a connected model would."""

    async def _run() -> None:
        settings = get_settings()
        result = await retrieve(
            build_store(),
            query,
            scope=_scope(project),
            token_budget=settings.retrieval_token_budget,
        )
        for obj, s in zip(result.objects, result.scores, strict=True):
            typer.echo(f"{s:.3f}  [{obj.confidence:.2f}] {obj.content}")

    asyncio.run(_run())


@app.command()
def compress(project: str | None = typer.Option(None)) -> None:
    """Run the compression job (§6) over one scope."""
    from coletar.jobs import compress as run_compress

    async def _run() -> None:
        report = await run_compress(build_store(), scope=_scope(project) if project else None)
        typer.echo(json.dumps(report.as_dict(), indent=2))

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
def seed() -> None:
    """Populate the store with the fixture graph: one object of every type, plus a
    supersedes chain. Useful for trying the Inspector and the compilers."""
    from coletar.seed import seed as run_seed

    async def _run() -> None:
        result = await run_seed(build_store())
        typer.echo(json.dumps(result.by_role, indent=2))

    asyncio.run(_run())


@app.command()
def history(object_id: str) -> None:
    """Replay one object's revisions from the event log — what a fact used to say
    and when it changed (§6). Reads the log only, never the object table."""
    from coletar.store.replay import replay_history

    async def _run() -> None:
        revisions = await replay_history(build_store(), object_id)
        if not revisions:
            typer.echo(f"no revisions recorded for {object_id}")
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
        store = InMemoryStore(embedder=embedder)
        ids = await seed_corpus(store, eval_set["corpus"])
        result = await run_eval(store, eval_set, ids)
        typer.echo(f"embedder: {embedder.model}")
        typer.echo(result.report())
        for miss in result.misses:
            typer.echo(f"  miss: {miss}")

    asyncio.run(_run())


@app.command()
def events(limit: int = 50) -> None:
    """Tail the Event/Revision Log — the raw feed behind the dashboard (§6)."""

    async def _run() -> None:
        for event in await build_store().list_events(limit=limit):
            typer.echo(
                f"{event.at.isoformat()}  {event.actor:<9} {event.type:<22} "
                f"{event.object_id or '-'}"
            )

    asyncio.run(_run())


if __name__ == "__main__":
    app()
