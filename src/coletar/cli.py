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
from typing import Any

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
def serve_inspector() -> None:
    """Run the read-only Context Inspector: upload a store snapshot, browse it."""
    from coletar.inspector.app import run

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
def compile(
    destination: str = typer.Option("local", help="Which provider compiler to run."),
    out: str = typer.Option("build/compile", help="Directory to write artifacts into."),
    base_model: str = typer.Option("llama3.1", help="FROM line for the Ollama Modelfile."),
    project: str | None = typer.Option(None, help="Compile one project scope only."),
    skip_review: bool = typer.Option(
        False, "--skip-review", help="Compile without the Inspector review gate."
    ),
    tenant: str | None = TENANT_OPTION,
) -> None:
    """True Migration: compile the graph into a destination's native containers.

    Reads the graph and writes files; it never mutates an object. The one thing it
    does write back is the `compile.run` event, because a compile is a fact about
    the graph's history even though it changed nothing in it.
    """
    from pathlib import Path

    from coletar.compiler import ChatGPTCompiler, ClaudeCompiler, LocalModelCompiler
    from coletar.inspector.review import review_status
    from coletar.schema.events import Actor, Event, EventType

    compilers: dict[str, type] = {
        "local": LocalModelCompiler,
        "claude": ClaudeCompiler,
        "chatgpt": ChatGPTCompiler,
    }
    if destination not in compilers:
        raise typer.BadParameter(
            f"unknown destination {destination!r}; have {sorted(compilers)}"
        )

    async def _run() -> None:
        resolved = _tenant(tenant)
        store = build_store()

        # The M5 gate. Enforced here rather than only in the Inspector's UI: a gate
        # one surface can walk around is not a gate, and the CLI is the surface an
        # automation would reach for.
        status = await review_status(store, resolved)
        if not status.can_compile and not skip_review:
            raise typer.BadParameter(
                f"{len(status.unreviewed)} of {len(status.eligible)} eligible objects "
                "have not been reviewed since they last changed. Run "
                "`coletar serve-inspector` and look at them, or pass --skip-review to "
                "compile anyway (recorded in the compile.run event)."
            )

        objects = await store.list_objects(
            resolved, scope=_scope(project) if project else None, limit=10_000
        )
        compiler = (
            LocalModelCompiler(base_model=base_model)
            if destination == "local"
            else compilers[destination]()
        )
        out_dir = Path(out)
        result = await compiler.compile(objects, out_dir=out_dir)

        await store.append_event(
            resolved,
            Event(
                type=EventType.COMPILE_RUN,
                actor=Actor.COMPILER,
                object_id=None,
                detail={
                    "destination": destination,
                    "out_dir": str(out_dir),
                    **result.manifest.summary(),
                    "continuity_score": result.score.total,
                    # An override has to be visible afterwards, or the gate teaches
                    # the user nothing about what was in the package.
                    "review_skipped": skip_review and not status.can_compile,
                    "unreviewed_at_compile": len(status.unreviewed),
                },
            ),
        )

        typer.echo(f"tenant: {resolved}")
        typer.echo(json.dumps(result.manifest.summary(), indent=2))
        typer.echo("")
        typer.echo(result.score.explain())
        typer.echo("")
        typer.echo(result.instructions)

    asyncio.run(_run())


@app.command()
def import_chatgpt(
    archive: str,
    project: str | None = typer.Option(None, help="Scope everything to one project."),
    tenant: str | None = TENANT_OPTION,
) -> None:
    """Import a ChatGPT export ZIP you downloaded yourself.

    Acquisition is human-initiated by design (§8.1): you click your own export button
    in ChatGPT's settings, OpenAI emails you the archive, and this starts once the
    file is on your disk. Nothing here touches a provider's site.
    """
    from pathlib import Path

    from coletar.acquisition.chatgpt_export import ChatGPTExportError, import_export

    async def _run() -> None:
        resolved = _tenant(tenant)
        try:
            report = await import_export(
                build_store(), resolved, Path(archive), scope=_scope(project)
            )
        except ChatGPTExportError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps({"tenant": resolved, **report.as_dict()}, indent=2))

    asyncio.run(_run())


@app.command()
def watch_downloads(
    directory: str = typer.Option("~/Downloads", help="Folder to watch for an export."),
    project: str | None = typer.Option(None, help="Scope everything to one project."),
    tenant: str | None = TENANT_OPTION,
) -> None:
    """Watch for a ChatGPT or Claude export you downloaded, and import it when it lands.

    coletar never asks a provider for anything (§8.1): you click your own export
    button, they email you a link, you download it. This only notices the file
    arriving so the import is not a second manual step.

    Both providers ship a file called `conversations.json` holding different shapes,
    so detection reads the structure and routes to the matching importer — a Claude
    export handed to the ChatGPT parser finds nothing and looks like success.
    """
    from pathlib import Path

    from coletar.acquisition import chatgpt_export, claude_export
    from coletar.acquisition.archive import store_archive
    from coletar.acquisition.watcher import POLL_SECONDS, detect, watch

    async def _run() -> None:
        resolved = _tenant(tenant)
        store = build_store()
        folder = Path(directory).expanduser()
        typer.echo(f"watching {folder} for a ChatGPT or Claude export (tenant {resolved})")

        # Two ImportReport types with the same shape; the CLI only renders them.
        importers: dict[str, Any] = {
            "chatgpt": chatgpt_export.import_export,
            "claude": claude_export.import_export,
        }

        async def on_export(path: Path) -> None:
            provider = detect(path)
            if provider is None:  # pragma: no cover - scan already filtered these
                return
            held = store_archive(path)
            typer.echo(f"  found a {provider} export: {path.name} -> {held.short_id}")
            report = await importers[provider](
                store, resolved, held.path, scope=_scope(project)
            )
            typer.echo(json.dumps(report.as_dict(), indent=2))

        await watch(folder, on_export, poll_seconds=POLL_SECONDS)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        typer.echo("stopped")


@app.command()
def import_claude(
    archive: str,
    project: str | None = typer.Option(None, help="Scope everything to one project."),
    tenant: str | None = TENANT_OPTION,
) -> None:
    """Import a claude.ai conversation export you downloaded yourself.

    Settings > Privacy > Export Data in Claude; Anthropic emails a link and this
    starts once the ZIP is on your disk (§8.1). Note that Claude's *memory* is not in
    that export — only conversations.
    """
    from pathlib import Path

    from coletar.acquisition.claude_export import ClaudeExportError, import_export

    async def _run() -> None:
        resolved = _tenant(tenant)
        try:
            report = await import_export(
                build_store(), resolved, Path(archive), scope=_scope(project)
            )
        except ClaudeExportError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps({"tenant": resolved, **report.as_dict()}, indent=2))

    asyncio.run(_run())


@app.command()
def mirror(
    out: str = typer.Option("~/coletar-vault", help="Where the Markdown vault lives."),
    pull: bool = typer.Option(False, "--pull", help="Apply edits made in the vault."),
    dry_run: bool = typer.Option(False, "--dry-run", help="With --pull, change nothing."),
    tenant: str | None = TENANT_OPTION,
) -> None:
    """Mirror the graph to Markdown you can open in Obsidian, or pull edits back.

    The vault is a projection: the typed graph stays canonical, because supersession,
    provenance and an immutable event log are things a directory of files cannot
    make true. `--pull` applies your edits through the same ingest path every other
    surface writes through, so they land as real events rather than as a silent
    change with no history behind it.
    """
    from pathlib import Path

    from coletar.mirror import mirror as run_mirror
    from coletar.mirror import pull_edits

    async def _run() -> None:
        resolved = _tenant(tenant)
        vault = Path(out).expanduser()
        store = build_store()
        if pull:
            report = await pull_edits(store, resolved, vault, dry_run=dry_run)
        else:
            report = await run_mirror(store, resolved, vault)  # type: ignore[assignment]
        typer.echo(json.dumps({"tenant": resolved, "vault": str(vault),
                               **report.as_dict()}, indent=2))

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
def propagation(
    tenant: str | None = TENANT_OPTION,
    trials: int = typer.Option(5, help="Facts to send in each direction."),
) -> None:
    """Prove the central claim: a memory written on one surface is readable on the
    other (§3.1). Writes through the local proxy's extraction path and reads through
    the MCP retrieval path, and back.

    Runnable on demand as well as in CI, because "memory is portable across models"
    is the one property worth being able to re-check at any moment.
    """
    from coletar.propagation import Direction, measure_round_trip
    from coletar.retrieval import retrieve
    from coletar.retrieval.embedding import tokenize

    outbound = [
        "I prefer fixed-point integers over doubles for money.",
        "From now on, always use uv instead of pip.",
        "Remember that Ledger deploys to Fly.io on every merge.",
        "I never use an ORM in this project.",
        "My name is Christopher, but everyone calls me Chris.",
    ][:trials]
    inbound = [
        "Priya owns the invoicing module and reviews its pull requests.",
        "Standups happen at 09:30 Lisbon time on Tuesdays.",
        "The staging database is restored from production every Sunday night.",
        "Design documents live in Notion, not in the repository.",
        "Chris bills hourly rather than per project.",
    ][:trials]

    async def _run() -> None:
        from coletar.extraction import extract_memories
        from coletar.schema.objects import ExtractionMethod, Memory, MemoryKind, OriginType

        resolved = _tenant(tenant)
        store = build_store()

        async def local_write(content: str) -> str:
            extracted = await extract_memories(user_text=content)
            memory = extracted[0] if extracted else Memory.from_write(content)
            return (await store.put_object(resolved, memory)).id

        async def connector_write(content: str) -> str:
            memory = Memory.from_write(
                content,
                kind=MemoryKind.FACT,
                extraction_method=ExtractionMethod.MCP_LIVE_WRITE,
                origin_type=OriginType.AGENT,
            )
            return (await store.put_object(resolved, memory)).id

        async def read(query: str) -> set[str]:
            context = await retrieve(store, resolved, query, top_k=25, trace=False)
            return {obj.id for obj in context.objects}

        report = await measure_round_trip(
            directions=[
                Direction("local->connector", local_write, read, outbound),
                Direction("connector->local", connector_write, read, inbound),
            ],
            query_for=lambda content: " ".join(tokenize(content)[:6]),
        )
        typer.echo(f"tenant {resolved}")
        typer.echo(report.report())
        raise typer.Exit(0 if report.propagated == report.total else 1)

    asyncio.run(_run())


@app.command()
def import_claude_code(
    tenant: str | None = TENANT_OPTION,
    rescan: bool = typer.Option(False, help="Re-read every transcript from the start."),
    dry_run: bool = typer.Option(False, help="Show what would be stored, store nothing."),
    project_scopes: bool = typer.Option(True, help="Scope memories by working directory."),
) -> None:
    """Import what you typed into Claude Code (§4.1).

    Claude Code writes every session to ~/.claude/projects as it works, so capture
    here is guaranteed rather than left to a model's discretion — no connector, no
    instruction snippet, no approval prompt. Only your own words are read; assistant
    replies and tool results are never mined.
    """
    from coletar.acquisition import default_root, import_sessions, iter_turns, session_files

    async def _run() -> None:
        resolved = _tenant(tenant)
        if dry_run:
            from coletar.acquisition import scope_for
            from coletar.extraction import extract_memories

            turns = found = 0
            for path in session_files(default_root()):
                for turn in iter_turns(path):
                    turns += 1
                    scope = scope_for(turn.cwd, project_scopes=project_scopes)
                    for memory in await extract_memories(user_text=turn.text, scope=scope):
                        found += 1
                        typer.echo(f"  [{memory.kind}] ({scope}) {memory.content}")
            typer.echo(f"\n{turns} turns read, {found} would be stored — nothing written")
            return

        report = await import_sessions(
            build_store(), resolved, rescan=rescan, project_scopes=project_scopes
        )
        typer.echo(f"tenant {resolved}")
        typer.echo(json.dumps(report.as_dict(), indent=2))

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
