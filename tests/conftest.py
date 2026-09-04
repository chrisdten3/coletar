"""Shared fixtures.

The relevance corpus lives in `fixtures/relevance_set.json` rather than in a test
module because three separate milestones measure against it: M1.2's top-5 bar,
M4.1's "compression must not drop a top-5 object", and M6.2's extraction quality.
One corpus, or the three numbers are not comparable.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pytest

from coletar.schema.objects import (
    GLOBAL_SCOPE,
    ContextObject,
    ExtractionMethod,
    Memory,
    MemoryKind,
    ObjectType,
    OriginType,
    Provenance,
    Provider,
    Scope,
    ScopeType,
)
from coletar.schema.tenancy import tenant_id
from coletar.store.memory import InMemoryStore

#: The tenant every non-tenancy test acts in. Isolation itself is proved in
#: test_tenancy.py; everything else simply has to name a tenant.
TENANT = tenant_id("tenant_test")

FIXTURES = Path(__file__).parent / "fixtures"

#: The export fixtures are synthetic — generated conversations, `someone@example.com`,
#: no one's real history — but they are still *zips*, and `.gitignore` refuses those
#: wholesale so that nobody can commit a genuine export by reflex. The consequence
#: was that 17 tests passed only on the machine that had built the archives once,
#: and were invisible everywhere else; CI found them missing on its first run.
#:
#: So the tracked artefact is the unpacked JSON, and the archive is rebuilt from it
#: at session start. The `*.zip` rule stays absolute, and the importer is still
#: exercised against a real ZIP rather than a directory.
# Not `exports/` — `.gitignore` excludes that name outright, which is the rule
# keeping real provider archives out of the repository. This is fixture source.
EXPORT_SOURCES = FIXTURES / "export_sources"


def _build_export_archives() -> None:
    import zipfile

    for source in sorted(EXPORT_SOURCES.iterdir()):
        if not source.is_dir():
            continue
        archive = FIXTURES / f"{source.name}_export.zip"
        members = sorted(source.iterdir())
        # Deterministic member order and timestamps: the watcher content-hashes the
        # archive to recognise a re-import, so a rebuild must not look like new data.
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for member in members:
                info = zipfile.ZipInfo(member.name, date_time=(2026, 8, 31, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, member.read_bytes())


_build_export_archives()


def scope_from(raw: str | None) -> Scope:
    return Scope(type=ScopeType.PROJECT, id=raw) if raw else GLOBAL_SCOPE


@dataclass(frozen=True)
class RelevanceSet:
    corpus: list[dict[str, object]]
    queries: list[dict[str, object]]


@pytest.fixture(scope="session")
def relevance_set() -> RelevanceSet:
    raw = json.loads((FIXTURES / "relevance_set.json").read_text())
    return RelevanceSet(corpus=raw["corpus"], queries=raw["queries"])


def build_corpus_object(item: dict[str, object]) -> ContextObject:
    scope = scope_from(item.get("scope"))  # type: ignore[arg-type]
    object_type = ObjectType(item["type"])
    content = str(item["content"])
    if object_type is ObjectType.MEMORY:
        return Memory.from_write(
            content,
            kind=MemoryKind(item["kind"]),
            scope=scope,
            extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
            origin_type=OriginType.USER,
        )
    return ContextObject(
        type=object_type,
        content=content,
        scope=scope,
        confidence=0.9,
        extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
        provenance=Provenance(origin_type=OriginType.USER, provider=Provider.COLETAR),
    )


@pytest.fixture
async def relevance_store(relevance_set: RelevanceSet) -> tuple[InMemoryStore, dict[str, str]]:
    """The corpus loaded into a store, plus a key->id map so a test can name the
    object it expects without depending on id generation."""
    store = InMemoryStore()
    ids: dict[str, str] = {}
    for item in relevance_set.corpus:
        stored = await store.put_object(TENANT, build_corpus_object(item))
        ids[str(item["key"])] = stored.id
    return store, ids


def _test_dsn() -> str:
    """Where the gated Postgres tests look for a database.

    The environment wins, then `.env`, then the compose default. Reading `.env` here
    is deliberate: it is where this project already keeps configuration, and without
    it every Postgres test skips silently while the developer believes they ran —
    which is worse than failing, because a green run means nothing.
    """
    from_env = os.environ.get("COLETAR_TEST_DATABASE_URL")
    if from_env:
        return from_env
    dotenv = Path(__file__).parent.parent / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "COLETAR_TEST_DATABASE_URL" and value.strip():
                return value.strip()
    return "postgresql://coletar:coletar@localhost:5433/coletar"


def _dsn_reachable(dsn: str, *, default_port: int = 5432) -> bool:
    parsed = urlparse(dsn)
    if parsed.hostname is None:
        return False
    try:
        with socket.create_connection(
            (parsed.hostname, parsed.port or default_port), timeout=1.0
        ):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def ollama_url() -> str:
    """A reachable Ollama with the configured embedding model pulled, or a skip.

    Gated for the same reason the Postgres fixture is: the point of these tests is
    that our assumptions about someone else's API are correct, and a mock would
    only re-assert the assumptions.
    """
    url = os.environ.get("COLETAR_TEST_OLLAMA_URL", "http://localhost:11434")
    if not _dsn_reachable(url.replace("http://", "//"), default_port=11434):
        pytest.skip(f"no Ollama reachable at {url}")
    try:
        import httpx

        names = {
            model["name"].split(":")[0]
            for model in httpx.get(f"{url}/api/tags", timeout=5.0).json().get("models", [])
        }
    except Exception:  # pragma: no cover - the skip path
        pytest.skip(f"could not list models at {url}")
    if "nomic-embed-text" not in names:
        pytest.skip("`ollama pull nomic-embed-text` to run the live embedder tests")
    return url


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    """A DSN for the Postgres backend, or a skip.

    Gated rather than mocked: a mocked database proves nothing about SQL. The suite
    stays green on a machine with no Docker, and these tests actually run in CI and
    anywhere `docker compose up` has been run.
    """
    dsn = _test_dsn()
    if not _dsn_reachable(dsn):
        pytest.skip(f"no Postgres reachable at {dsn} — run `docker compose up -d`")
    return dsn

