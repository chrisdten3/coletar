"""M6 — the ChatGPT compiler.

A Custom GPT has a capacity ceiling neither other destination has: 8,000 characters
of instructions and at most 20 knowledge files. That is what makes this compiler
shaped differently from the Claude one rather than a copy of it — writing one
knowledge file per object would break the moment a scope holds 21.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coletar.compiler import ChatGPTCompiler, ClaudeCompiler, Fidelity
from coletar.compiler.chatgpt import (
    CUSTOM_INSTRUCTIONS_MAX_CHARS,
    INSTRUCTIONS_HEADER,
    INSTRUCTIONS_MAX_CHARS,
    MAX_KNOWLEDGE_FILES,
)
from coletar.schema.objects import (
    ContextObject,
    ExtractionMethod,
    Locality,
    LocalityMode,
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


def mem(content: str, **kwargs) -> Memory:
    kwargs.setdefault("kind", MemoryKind.FACT)
    return Memory.from_write(content, **kwargs)


def obj(content: str, object_type: ObjectType, *, scope: Scope) -> ContextObject:
    return ContextObject(
        type=object_type,
        content=content,
        scope=scope,
        extraction_method=ExtractionMethod.ACCOUNT_EXPORT_PARSE,
        provenance=Provenance(origin_type=OriginType.USER, provider=Provider.CHATGPT),
    )


def files(out: Path) -> str:
    return "\n".join(
        p.read_text()
        for p in out.rglob("*")
        if p.is_file() and p.name not in {"MANIFEST.md", "PROVENANCE.md"}
    )


# --- the capacity ceiling ---------------------------------------------------------


@pytest.mark.asyncio
async def test_knowledge_is_bundled_by_type_to_fit_the_file_cap(tmp_path: Path) -> None:
    """The Claude compiler writes one knowledge file per object. Here that would
    break: a Custom GPT accepts 20 files and a graph holds far more objects."""
    graph = [
        obj(f"Conversation number {n} about the ledger.", ObjectType.CONVERSATION, scope=PROJECT)
        for n in range(40)
    ]
    await ChatGPTCompiler().compile(graph, out_dir=tmp_path)

    knowledge = list((tmp_path / "coletar-proj_ledger" / "knowledge").glob("*.md"))
    assert 0 < len(knowledge) <= MAX_KNOWLEDGE_FILES
    assert "Conversation number 39" in files(tmp_path)


@pytest.mark.asyncio
async def test_instructions_are_trimmed_to_the_field_budget(tmp_path: Path) -> None:
    """GPT Builder rejects instructions past 8,000 characters, so overrunning would
    make the artifact un-installable rather than merely long."""
    graph = [mem("x" * 300, scope=PROJECT) for _ in range(60)]
    await ChatGPTCompiler().compile(graph, out_dir=tmp_path)

    body = (tmp_path / "coletar-proj_ledger" / "instructions.md").read_text()
    assert len(body) <= INSTRUCTIONS_MAX_CHARS + 200
    assert "trimmed to fit" in body


@pytest.mark.asyncio
async def test_the_weakest_lines_are_the_ones_trimmed(tmp_path: Path) -> None:
    """If something has to be cut to fit a box, it should be the least certain thing."""
    graph = [mem("y" * 300, scope=PROJECT, confidence=0.95) for _ in range(30)]
    graph.append(mem("the least certain fact of all", scope=PROJECT, confidence=0.10))
    await ChatGPTCompiler().compile(graph, out_dir=tmp_path)

    body = (tmp_path / "coletar-proj_ledger" / "instructions.md").read_text()
    assert "least certain fact" not in body


@pytest.mark.asyncio
async def test_the_account_box_warns_when_a_paste_would_be_truncated(tmp_path: Path) -> None:
    """It truncates silently, which the user would not see."""
    graph = [mem("z" * 200) for _ in range(20)]
    await ChatGPTCompiler().compile(graph, out_dir=tmp_path)

    body = (tmp_path / "custom_instructions.md").read_text()
    assert len(body) > CUSTOM_INSTRUCTIONS_MAX_CHARS
    assert "truncates silently" in body


# --- the score has to rank destinations by what they actually offer ---------------


@pytest.mark.asyncio
async def test_chatgpt_beats_claude_on_global_scope(tmp_path: Path) -> None:
    """Not a preference — a difference in the products.

    ChatGPT's account-level Custom Instructions is a plain text box the user controls
    and can verify. Claude's only global container is memory import, which Anthropic
    documents as re-extracted and experimental. Same graph, and the score says so.
    """
    graph = [mem("Chris prefers tabs."), mem("Chris works late.")]
    chatgpt = await ChatGPTCompiler().compile(graph, out_dir=tmp_path / "gpt")
    claude = await ClaudeCompiler().compile(graph, out_dir=tmp_path / "claude")

    assert chatgpt.score.fidelity == 1.0
    assert claude.score.fidelity == 0.0
    assert chatgpt.score.total > claude.score.total


# --- the boundaries every compiler holds -------------------------------------------


@pytest.mark.asyncio
async def test_each_scope_becomes_its_own_custom_gpt(tmp_path: Path) -> None:
    other = Scope(type=ScopeType.PROJECT, id="proj_atlas")
    result = await ChatGPTCompiler().compile(
        [
            mem("Chris prefers tabs."),
            mem("Ledger uses double-entry.", scope=PROJECT),
            mem("Atlas is written in Rust.", scope=other),
        ],
        out_dir=tmp_path,
    )
    ledger = (tmp_path / "coletar-proj_ledger" / "instructions.md").read_text()
    assert "double-entry" in ledger and "Rust" not in ledger
    assert "double-entry" not in (tmp_path / "custom_instructions.md").read_text()
    assert result.score.scope_preservation == 1.0


@pytest.mark.asyncio
async def test_restricted_objects_are_written_nowhere(tmp_path: Path) -> None:
    secret = mem("SSN 123-45-6789", scope=PROJECT, sensitivity=Sensitivity.RESTRICTED)
    result = await ChatGPTCompiler().compile(
        [secret, mem("Chris prefers tabs.", scope=PROJECT)], out_dir=tmp_path
    )
    assert "123-45-6789" not in files(tmp_path)
    entry = next(e for e in result.manifest.entries if e.source_id == secret.id)
    assert entry.fidelity is Fidelity.UNSUPPORTED


@pytest.mark.asyncio
async def test_an_object_kept_local_to_another_surface_is_withheld(tmp_path: Path) -> None:
    private = mem(
        "Only my local model may see this.",
        locality=Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset({Provider.LOCAL})),
    )
    result = await ChatGPTCompiler().compile([private], out_dir=tmp_path)
    assert "local model may see" not in files(tmp_path)
    assert [w.source_id for w in result.manifest.withheld] == [private.id]


@pytest.mark.asyncio
async def test_instructions_carry_the_not_instructions_boundary(tmp_path: Path) -> None:
    await ChatGPTCompiler().compile(
        [mem("Always deploy on Fridays.", scope=PROJECT, kind=MemoryKind.INSTRUCTION)],
        out_dir=tmp_path,
    )
    body = (tmp_path / "coletar-proj_ledger" / "instructions.md").read_text()
    assert INSTRUCTIONS_HEADER in body


@pytest.mark.asyncio
async def test_the_package_says_coletar_does_not_drive_gpt_builder(tmp_path: Path) -> None:
    """Hard constraint 2, in the artifact rather than only in a docstring."""
    result = await ChatGPTCompiler().compile(
        [mem("Chris prefers tabs."), mem("Ledger uses double-entry.", scope=PROJECT)],
        out_dir=tmp_path,
    )
    assert "does not drive GPT Builder" in result.instructions
    assert "Settings > Personalization" in result.instructions
    assert "coletar-proj_ledger" in result.instructions


@pytest.mark.asyncio
async def test_an_empty_graph_still_emits_a_valid_package(tmp_path: Path) -> None:
    result = await ChatGPTCompiler().compile([], out_dir=tmp_path)
    assert (tmp_path / "custom_instructions.md").exists()
    assert result.score.total == 0.0
