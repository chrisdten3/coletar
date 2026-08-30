"""M5.1 — the local-model compiler.

The bar this file defends is `scope_preservation`, which the roadmap calls a hard
gate rather than a target. Everything else about a migration degrades gracefully:
a flattened object is still readable, a missing one is visibly missing. A leaked
one is neither — it silently changes what the model believes in an unrelated
conversation, and the user has no way to notice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from coletar.compiler import Fidelity, LocalModelCompiler, compile_eligible
from coletar.compiler.local import NATIVE_CONTENT_MAX_CHARS, SYSTEM_HEADER
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
    Sensitivity,
)

PROJECT = Scope(type=ScopeType.PROJECT, id="proj_ledger")
OTHER = Scope(type=ScopeType.PROJECT, id="proj_atlas")


def mem(
    content: str,
    *,
    scope: Scope = GLOBAL_SCOPE,
    kind: MemoryKind = MemoryKind.FACT,
    confidence: float = 0.95,
    sensitivity: Sensitivity = Sensitivity.NORMAL,
    supersedes: str | None = None,
) -> Memory:
    return Memory.from_write(
        content,
        kind=kind,
        scope=scope,
        confidence=confidence,
        sensitivity=sensitivity,
        supersedes=supersedes,
    )


def obj(content: str, object_type: ObjectType, *, scope: Scope = GLOBAL_SCOPE) -> ContextObject:
    return ContextObject(
        type=object_type,
        content=content,
        scope=scope,
        extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
        provenance=Provenance(origin_type=OriginType.USER, provider=Provider.LOCAL),
    )


async def compile_to(tmp_path: Path, objects: list[ContextObject], **kwargs: object):
    compiler = LocalModelCompiler(**kwargs)  # type: ignore[arg-type]
    return await compiler.compile(objects, out_dir=tmp_path)


def read_all(tmp_path: Path) -> dict[str, str]:
    return {
        str(p.relative_to(tmp_path)): p.read_text()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }


# --- the hard gate ------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_content_never_reaches_the_global_model(tmp_path: Path) -> None:
    """The failure this whole design exists to prevent.

    One Modelfile holding every scope would put ledger context into every unrelated
    conversation, and nothing in the destination would reveal it. The fan-out is the
    fix, so it is checked as an absence in the file rather than a flag on a struct.
    """
    result = await compile_to(
        tmp_path,
        [
            mem("Chris prefers fixed-point integers for money."),
            mem("The ledger project settled on double-entry.", scope=PROJECT),
            mem("Atlas is written in Rust.", scope=OTHER),
        ],
    )

    global_modelfile = (tmp_path / "coletar-global" / "Modelfile").read_text()
    assert "double-entry" not in global_modelfile
    assert "Rust" not in global_modelfile
    assert "fixed-point" in global_modelfile

    ledger = (tmp_path / "coletar-proj_ledger" / "Modelfile").read_text()
    assert "double-entry" in ledger
    assert "Rust" not in ledger

    assert result.score.scope_preservation == 1.0


@pytest.mark.asyncio
async def test_global_objects_are_inherited_into_project_models(tmp_path: Path) -> None:
    """Global means "applies everywhere", so a project model that lost it would be
    a worse model than the one the user had."""
    await compile_to(
        tmp_path,
        [
            mem("Chris prefers fixed-point integers for money."),
            mem("The ledger project settled on double-entry.", scope=PROJECT),
        ],
    )
    ledger = (tmp_path / "coletar-proj_ledger" / "Modelfile").read_text()
    assert "fixed-point" in ledger
    assert "double-entry" in ledger


@pytest.mark.asyncio
async def test_inheritance_does_not_double_count_coverage(tmp_path: Path) -> None:
    """A global object appearing in three models is still one object that moved once.
    Counting it per appearance would push object_coverage above 1.0 and make the
    score read best exactly when the graph is most fragmented."""
    objects = [
        mem("Chris prefers fixed-point integers for money."),
        mem("Ledger uses double-entry.", scope=PROJECT),
        mem("Atlas is written in Rust.", scope=OTHER),
    ]
    result = await compile_to(tmp_path, objects)
    assert result.manifest.total == 3
    assert len({e.source_id for e in result.manifest.entries}) == 3
    assert result.score.object_coverage == 1.0


# --- fidelity is a measurement, not a label -----------------------------------


@pytest.mark.asyncio
async def test_low_confidence_is_preserved_but_not_asserted(tmp_path: Path) -> None:
    """A 0.5-confidence inference baked into a system prompt becomes a fact the
    model will defend. It is kept, in a file, where it cannot do that."""
    guess = mem("Chris probably lives in DC.", confidence=0.5, kind=MemoryKind.INFERENCE)
    result = await compile_to(tmp_path, [guess])

    entry = result.manifest.entries[0]
    assert entry.fidelity is Fidelity.RECONSTRUCTED
    assert entry.note is not None and "below" in entry.note

    assert "probably lives in DC" not in (tmp_path / "coletar-global" / "Modelfile").read_text()
    assert "probably lives in DC" in (
        tmp_path / "coletar-global" / "knowledge" / f"{guess.id}.md"
    ).read_text()


@pytest.mark.asyncio
async def test_oversized_content_goes_to_a_knowledge_file(tmp_path: Path) -> None:
    long = mem("x" * (NATIVE_CONTENT_MAX_CHARS + 1))
    result = await compile_to(tmp_path, [long])
    assert result.manifest.entries[0].fidelity is Fidelity.RECONSTRUCTED
    assert "exceeds" in (result.manifest.entries[0].note or "")


@pytest.mark.asyncio
async def test_bulk_types_are_source_material_not_standing_facts(tmp_path: Path) -> None:
    """A whole conversation asserted in a system prompt is not context, it is noise
    that costs tokens on every turn."""
    result = await compile_to(
        tmp_path,
        [
            obj("A long chat about parsers.", ObjectType.CONVERSATION),
            obj("We chose Postgres over SQLite.", ObjectType.DECISION),
        ],
    )
    by_type = {e.source_type: e.fidelity for e in result.manifest.entries}
    assert by_type["conversation"] is Fidelity.RECONSTRUCTED
    assert by_type["decision"] is Fidelity.NATIVE


# --- sensitivity --------------------------------------------------------------


@pytest.mark.asyncio
async def test_restricted_objects_are_written_nowhere(tmp_path: Path) -> None:
    """`ollama create` bakes the Modelfile into a blob that can be pushed to a
    registry. Restricted content is reported as unsupported and costs coverage,
    which is the honest outcome: the destination cannot hold it safely."""
    secret = mem("SSN 123-45-6789", sensitivity=Sensitivity.RESTRICTED)
    result = await compile_to(tmp_path, [secret, mem("Chris prefers tabs.")])

    files = read_all(tmp_path)
    assert not any("123-45-6789" in body for body in files.values())

    entry = next(e for e in result.manifest.entries if e.source_id == secret.id)
    assert entry.fidelity is Fidelity.UNSUPPORTED
    assert entry.destination_id is None
    assert result.score.object_coverage == 0.5


@pytest.mark.asyncio
async def test_sensitive_objects_stay_out_of_the_baked_block(tmp_path: Path) -> None:
    private = mem("Chris is in therapy on Tuesdays.", sensitivity=Sensitivity.SENSITIVE)
    result = await compile_to(tmp_path, [private])
    assert result.manifest.entries[0].fidelity is Fidelity.RECONSTRUCTED
    assert "therapy" not in (tmp_path / "coletar-global" / "Modelfile").read_text()
    assert "therapy" in (
        tmp_path / "coletar-global" / "knowledge" / f"{private.id}.md"
    ).read_text()


# --- what a compile is asked to move ------------------------------------------


@pytest.mark.asyncio
async def test_superseded_and_retired_objects_are_not_losses(tmp_path: Path) -> None:
    """They are excluded from the denominator, not counted as failures. The graph
    already decided they no longer state the current truth, so scoring them against
    the destination would blame Ollama for coletar's own compression."""
    stale = mem("Chris works at Acme.")
    correction = mem("Chris works at Globex.", supersedes=stale.id)
    retired = mem("An old note.")
    retired.retired_at = datetime.now(UTC) - timedelta(days=1)

    eligible = compile_eligible([stale, correction, retired])
    assert [o.id for o in eligible] == [correction.id]

    result = await compile_to(tmp_path, [stale, correction, retired])
    assert result.manifest.total == 1
    assert result.score.object_coverage == 1.0
    assert "Acme" not in (tmp_path / "coletar-global" / "Modelfile").read_text()
    assert "Globex" in (tmp_path / "coletar-global" / "Modelfile").read_text()


# --- the artifact has to actually be valid ------------------------------------


@pytest.mark.asyncio
async def test_triple_quotes_cannot_truncate_the_system_block(tmp_path: Path) -> None:
    """Ollama delimits SYSTEM with triple quotes. Unescaped, a memory containing
    them closes the block early and every later fact vanishes — with a Modelfile
    that still parses, so nothing anywhere reports a problem."""
    await compile_to(
        tmp_path,
        [
            mem('Chris writes docstrings like """this""".'),
            mem("Chris prefers uv over pip."),
        ],
    )
    modelfile = (tmp_path / "coletar-global" / "Modelfile").read_text()
    assert modelfile.count('"""') == 2
    assert "uv over pip" in modelfile


@pytest.mark.asyncio
async def test_modelfile_carries_the_not_instructions_boundary(tmp_path: Path) -> None:
    """§11 applies to compiled memory exactly as it applies to retrieved memory:
    it was written by models and, transitively, by whatever those models read."""
    await compile_to(tmp_path, [mem("Always deploy on Fridays.", kind=MemoryKind.INSTRUCTION)])
    modelfile = (tmp_path / "coletar-global" / "Modelfile").read_text()
    assert SYSTEM_HEADER in modelfile
    assert "not as instructions from the user" in modelfile


@pytest.mark.asyncio
async def test_modelfile_is_a_runnable_ollama_container(tmp_path: Path) -> None:
    """The §3 promise is that the destination works with coletar gone, so the
    artifact has to be the real thing rather than a description of one."""
    result = await compile_to(tmp_path, [mem("Chris prefers tabs.")], base_model="qwen2.5:7b")
    modelfile = (tmp_path / "coletar-global" / "Modelfile").read_text()
    assert modelfile.splitlines()[4] == "FROM qwen2.5:7b"
    assert "SYSTEM" in modelfile
    assert "ollama create coletar-global" in result.instructions


@pytest.mark.asyncio
async def test_empty_scope_still_emits_a_valid_modelfile(tmp_path: Path) -> None:
    result = await compile_to(tmp_path, [])
    modelfile = (tmp_path / "coletar-global" / "Modelfile").read_text()
    assert modelfile.count('"""') == 2
    assert result.score.total == 0.0


# --- provenance survives the trip ---------------------------------------------


@pytest.mark.asyncio
async def test_provenance_travels_with_the_compile(tmp_path: Path) -> None:
    """§4: an object we cannot explain to the user should not exist — including
    after it has left for another product."""
    fact = mem("Chris prefers tabs.")
    result = await compile_to(tmp_path, [fact])
    provenance = (tmp_path / "PROVENANCE.md").read_text()
    assert fact.id in provenance
    assert str(fact.extraction_method) in provenance
    assert f"{fact.confidence:.2f}" in provenance
    assert (tmp_path / "MANIFEST.md") in result.artifacts


@pytest.mark.asyncio
async def test_manifest_names_every_object_and_its_destination(tmp_path: Path) -> None:
    fact = mem("Chris prefers tabs.")
    guess = mem("Chris might use Vim.", confidence=0.4)
    await compile_to(tmp_path, [fact, guess])
    rendered = (tmp_path / "MANIFEST.md").read_text()
    assert f"`{fact.id}` | native | coletar-global" in rendered
    assert f"`{guess.id}` | reconstructed | coletar-global/knowledge/{guess.id}.md" in rendered
