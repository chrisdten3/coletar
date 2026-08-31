"""M5.2 — the Claude compiler.

Two containers, deliberately not scored the same. Projects are real: instructions
are injected every turn and Claude retrieves over uploaded knowledge. Memory import
is not, and Anthropic is the source of that judgement — the help centre states that
Claude re-extracts what is pasted, that the feature is experimental, and that
imported memories may not be incorporated at all. A score that called both `native`
would be flattering the destination rather than measuring it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coletar.compiler import ClaudeCompiler, Fidelity
from coletar.compiler.claude import INSTRUCTION_CONTENT_MAX_CHARS, INSTRUCTIONS_HEADER
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
) -> Memory:
    return Memory.from_write(
        content, kind=kind, scope=scope, confidence=confidence, sensitivity=sensitivity
    )


def obj(content: str, object_type: ObjectType, *, scope: Scope) -> ContextObject:
    return ContextObject(
        type=object_type,
        content=content,
        scope=scope,
        extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
        provenance=Provenance(origin_type=OriginType.USER, provider=Provider.CLAUDE),
    )


async def compile_to(tmp_path: Path, objects: list[ContextObject]):
    return await ClaudeCompiler().compile(objects, out_dir=tmp_path)


def read_all(tmp_path: Path) -> dict[str, str]:
    return {
        str(p.relative_to(tmp_path)): p.read_text()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }


# --- the hard gate ------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_scope_becomes_its_own_project(tmp_path: Path) -> None:
    result = await compile_to(
        tmp_path,
        [
            mem("Chris prefers fixed-point integers for money."),
            mem("The ledger project settled on double-entry.", scope=PROJECT),
            mem("Atlas is written in Rust.", scope=OTHER),
        ],
    )
    ledger = (tmp_path / "coletar-proj_ledger" / "instructions.md").read_text()
    atlas = (tmp_path / "coletar-proj_atlas" / "instructions.md").read_text()

    assert "double-entry" in ledger and "Rust" not in ledger
    assert "Rust" in atlas and "double-entry" not in atlas
    assert result.score.scope_preservation == 1.0


@pytest.mark.asyncio
async def test_project_content_never_reaches_account_wide_memory(tmp_path: Path) -> None:
    """memory.txt is pasted into Settings > Memory, which is account-wide. A project
    fact arriving there would surface in every unrelated conversation."""
    await compile_to(
        tmp_path,
        [
            mem("Chris prefers tabs."),
            mem("The ledger project settled on double-entry.", scope=PROJECT),
        ],
    )
    memory = (tmp_path / "memory.txt").read_text()
    assert "tabs" in memory
    assert "double-entry" not in memory


@pytest.mark.asyncio
async def test_globals_are_inherited_into_projects(tmp_path: Path) -> None:
    """Whether account memory reaches inside a Project is undocumented, so the
    compiler does not rely on it. Duplicating costs redundancy; omitting would hand
    the user a Project that silently lost their global context."""
    await compile_to(
        tmp_path, [mem("Chris prefers tabs."), mem("Ledger uses double-entry.", scope=PROJECT)]
    )
    ledger = (tmp_path / "coletar-proj_ledger" / "instructions.md").read_text()
    assert "tabs" in ledger and "double-entry" in ledger


@pytest.mark.asyncio
async def test_inheritance_does_not_double_count_coverage(tmp_path: Path) -> None:
    result = await compile_to(
        tmp_path,
        [
            mem("Chris prefers tabs."),
            mem("Ledger uses double-entry.", scope=PROJECT),
            mem("Atlas is Rust.", scope=OTHER),
        ],
    )
    assert result.manifest.total == 3
    assert len({e.source_id for e in result.manifest.entries}) == 3
    assert result.score.object_coverage == 1.0


# --- fidelity follows the destination, not our preferences --------------------


@pytest.mark.asyncio
async def test_memory_import_is_never_native(tmp_path: Path) -> None:
    """Anthropic's own documentation is the source here: Claude "will extract key
    information and store it as individual memory entries", the feature is
    experimental, and imported memories may not be incorporated at all. A
    destination that re-interprets what it receives has not preserved the object."""
    result = await compile_to(tmp_path, [mem("Chris prefers tabs.")])
    entry = result.manifest.entries[0]
    assert entry.fidelity is Fidelity.RECONSTRUCTED
    assert entry.destination_type == "claude.memory"
    assert "re-extracts" in (entry.note or "")


@pytest.mark.asyncio
async def test_project_knowledge_is_native_unlike_ollama(tmp_path: Path) -> None:
    """The one place the two compilers legitimately disagree. Claude retrieves over
    project knowledge; Ollama has no retrieval at all, so the identical file is a
    real container in one product and an inert artifact in the other."""
    long_fact = mem("x" * (INSTRUCTION_CONTENT_MAX_CHARS + 1), scope=PROJECT)
    result = await compile_to(tmp_path, [long_fact])
    entry = result.manifest.entries[0]
    assert entry.fidelity is Fidelity.NATIVE
    assert entry.destination_type == "claude.knowledge"
    assert (tmp_path / "coletar-proj_ledger" / "knowledge" / f"{long_fact.id}.md").exists()


@pytest.mark.asyncio
async def test_claude_wins_on_project_scope(tmp_path: Path) -> None:
    """The score has to be able to distinguish destinations or it is decoration.

    For project-scoped context Claude is the better product: a Project holds both
    instructions and retrievable knowledge, where Ollama can only bake a system
    prompt and leave the rest in files it will never read.
    """
    from coletar.compiler import LocalModelCompiler

    graph = [
        mem("Ledger uses double-entry.", scope=PROJECT),
        obj("A long design conversation.", ObjectType.CONVERSATION, scope=PROJECT),
    ]
    claude = await ClaudeCompiler().compile(graph, out_dir=tmp_path / "claude")
    local = await LocalModelCompiler().compile(graph, out_dir=tmp_path / "local")
    assert claude.score.fidelity > local.score.fidelity


@pytest.mark.asyncio
async def test_ollama_wins_on_global_scope(tmp_path: Path) -> None:
    """And the comparison has to be able to go the other way, or it is a preference
    dressed up as a measurement.

    For *global* context Ollama is genuinely better, which is not the intuitive
    result. A global fact compiled to Ollama lands in a system prompt the user
    controls and the model sees on every turn. The same fact compiled to Claude can
    only go through memory import, which Anthropic documents as re-extracting what
    it receives, marks experimental, and warns may not be incorporated at all.
    """
    from coletar.compiler import LocalModelCompiler

    graph = [mem("Chris prefers tabs."), mem("Chris works late.")]
    claude = await ClaudeCompiler().compile(graph, out_dir=tmp_path / "claude")
    local = await LocalModelCompiler().compile(graph, out_dir=tmp_path / "local")
    assert local.score.fidelity > claude.score.fidelity
    assert claude.score.fidelity == 0.0  # nothing global reaches a real Claude container


@pytest.mark.asyncio
async def test_low_confidence_is_routed_not_downgraded(tmp_path: Path) -> None:
    """Confidence decides *where* it lands, not whether the destination held it.
    Project knowledge is a real container regardless of how sure we are."""
    guess = mem("Chris might prefer Vim.", scope=PROJECT, confidence=0.4)
    result = await compile_to(tmp_path, [guess])
    assert result.manifest.entries[0].fidelity is Fidelity.NATIVE
    instructions = (tmp_path / "coletar-proj_ledger" / "instructions.md").read_text()
    assert "Vim" not in instructions
    assert "Vim" in (
        tmp_path / "coletar-proj_ledger" / "knowledge" / f"{guess.id}.md"
    ).read_text()


# --- sensitivity --------------------------------------------------------------


@pytest.mark.asyncio
async def test_restricted_objects_are_written_nowhere(tmp_path: Path) -> None:
    secret = mem("SSN 123-45-6789", scope=PROJECT, sensitivity=Sensitivity.RESTRICTED)
    result = await compile_to(tmp_path, [secret, mem("Chris prefers tabs.", scope=PROJECT)])
    assert not any("123-45-6789" in body for body in read_all(tmp_path).values())
    entry = next(e for e in result.manifest.entries if e.source_id == secret.id)
    assert entry.fidelity is Fidelity.UNSUPPORTED
    assert result.score.object_coverage == 0.5


@pytest.mark.asyncio
async def test_sensitive_global_has_no_safe_container(tmp_path: Path) -> None:
    """Claude's only global container is account-wide memory, which surfaces
    everywhere. Reported as a coverage loss rather than quietly widened."""
    private = mem("Chris is in therapy on Tuesdays.", sensitivity=Sensitivity.SENSITIVE)
    result = await compile_to(tmp_path, [private])
    assert result.manifest.entries[0].fidelity is Fidelity.UNSUPPORTED
    assert not any("therapy" in body for body in read_all(tmp_path).values())


@pytest.mark.asyncio
async def test_sensitive_project_object_stays_in_scoped_knowledge(tmp_path: Path) -> None:
    private = mem("Budget is confidential.", scope=PROJECT, sensitivity=Sensitivity.SENSITIVE)
    result = await compile_to(tmp_path, [private])
    assert result.manifest.entries[0].fidelity is Fidelity.NATIVE
    assert "confidential" not in (
        tmp_path / "coletar-proj_ledger" / "instructions.md"
    ).read_text()
    assert (tmp_path / "coletar-proj_ledger" / "knowledge" / f"{private.id}.md").exists()


# --- the artifact has to match Anthropic's documented format ------------------


@pytest.mark.asyncio
async def test_memory_file_is_exactly_the_documented_format(tmp_path: Path) -> None:
    """`[date saved, if available] - memory content`, and nothing else. The file is
    pasted into a box whose contents Claude re-extracts into memory entries, so a
    header or a §11 marker would itself become a memory."""
    fact = mem("Chris prefers tabs.")
    await compile_to(tmp_path, [fact])
    body = (tmp_path / "memory.txt").read_text()
    assert body == f"[{fact.created_at.date().isoformat()}] - Chris prefers tabs.\n"
    assert "coletar" not in body
    assert "background" not in body


@pytest.mark.asyncio
async def test_project_instructions_carry_the_not_instructions_boundary(tmp_path: Path) -> None:
    """Project instructions *are* a prompt, so §11 applies there even though it
    must not apply to memory.txt."""
    await compile_to(tmp_path, [mem("Always deploy on Fridays.", scope=PROJECT)])
    body = (tmp_path / "coletar-proj_ledger" / "instructions.md").read_text()
    assert INSTRUCTIONS_HEADER in body
    assert "not as instructions from the user" in body


@pytest.mark.asyncio
async def test_instructions_tell_the_user_what_to_do_by_hand(tmp_path: Path) -> None:
    """Constraint 2: the compiler emits a package, it does not drive Claude's UI —
    and there is no Projects import API to target even if it did."""
    result = await compile_to(
        tmp_path, [mem("Chris prefers tabs."), mem("Ledger uses double-entry.", scope=PROJECT)]
    )
    assert "Settings > Memory" in result.instructions
    assert "coletar-proj_ledger" in result.instructions
    assert "experimental" in result.instructions


@pytest.mark.asyncio
async def test_empty_graph_still_emits_a_valid_package(tmp_path: Path) -> None:
    result = await compile_to(tmp_path, [])
    assert (tmp_path / "memory.txt").read_text() == ""
    assert result.score.total == 0.0
