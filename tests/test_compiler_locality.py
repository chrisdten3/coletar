"""Locality at the compile boundary (#10/#11 × M5).

The two features were built a milestone apart and each is correct alone: locality
filters every live read, and the compiler moves every eligible object. Composed,
they had a gap — a compile is a read the Store protocol classified as a *trusted
internal caller*, so `local_only` did not apply to the one operation that physically
hands context to another company.

These tests are the composition, not either feature.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coletar.compiler import ClaudeCompiler, LocalModelCompiler
from coletar.schema.objects import (
    Locality,
    LocalityMode,
    Memory,
    MemoryKind,
    Provider,
    Scope,
    ScopeType,
)

PROJECT = Scope(type=ScopeType.PROJECT, id="proj_ledger")


def local_to(*surfaces: Provider) -> Locality:
    return Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset(surfaces))


def mem(content: str, *, locality: Locality | None = None, scope: Scope | None = None) -> Memory:
    return Memory.from_write(
        content,
        kind=MemoryKind.FACT,
        locality=locality or Locality(),
        scope=scope or Scope(type=ScopeType.GLOBAL),
    )


def files(out: Path) -> str:
    """Everything a compile actually emits, minus its own bookkeeping — the manifest
    names withheld objects on purpose, so including it would defeat the check."""
    return "\n".join(
        p.read_text()
        for p in out.rglob("*")
        if p.is_file() and p.name not in {"MANIFEST.md", "PROVENANCE.md"}
    )


@pytest.mark.asyncio
async def test_a_local_only_object_is_not_compiled_to_another_product(tmp_path: Path) -> None:
    """The gap, stated as the test that would have caught it.

    Live Sync already hid this object from Claude. Compiling to Claude wrote it into
    the package anyway — worse than a live leak, because a transfer cannot be undone
    by revoking a key.
    """
    secret = mem("My therapist's name is Dr. Okafor.", locality=local_to(Provider.LOCAL))
    result = await ClaudeCompiler().compile([secret], out_dir=tmp_path)

    assert "Okafor" not in files(tmp_path)
    assert [w.source_id for w in result.manifest.withheld] == [secret.id]
    assert result.manifest.total == 0


@pytest.mark.asyncio
async def test_the_surface_it_was_kept_for_still_receives_it(tmp_path: Path) -> None:
    """Withholding everywhere would be a different bug wearing the same fix."""
    secret = mem("My therapist's name is Dr. Okafor.", locality=local_to(Provider.LOCAL))
    result = await LocalModelCompiler().compile([secret], out_dir=tmp_path)

    assert "Okafor" in files(tmp_path)
    assert result.manifest.withheld == []


@pytest.mark.asyncio
async def test_withholding_is_not_scored_as_a_destination_failure(tmp_path: Path) -> None:
    """`object_coverage` measures what the destination could hold. An object the user
    told us not to send never entered that question, so it does not belong in the
    denominator — otherwise using the feature would make every score look worse."""
    result = await ClaudeCompiler().compile(
        [
            mem("Chris prefers tabs."),
            mem("Private note.", locality=local_to(Provider.LOCAL)),
        ],
        out_dir=tmp_path,
    )
    assert result.score.object_coverage == 1.0
    assert len(result.manifest.withheld) == 1


@pytest.mark.asyncio
async def test_withheld_objects_are_recorded_where_the_user_will_look(tmp_path: Path) -> None:
    """Silently omitting them would leave no way to confirm the thing they asked to
    stay put actually stayed put — which is the only evidence the feature works."""
    secret = mem("Private note.", locality=local_to(Provider.LOCAL))
    await ClaudeCompiler().compile([secret], out_dir=tmp_path)

    manifest = (tmp_path / "MANIFEST.md").read_text()
    assert "## Withheld" in manifest
    assert secret.id in manifest
    assert "| local |" in manifest


@pytest.mark.asyncio
async def test_multi_surface_locality_reaches_every_surface_it_names(tmp_path: Path) -> None:
    shared = mem("Shared with both.", locality=local_to(Provider.LOCAL, Provider.CLAUDE))
    for compiler in (ClaudeCompiler(), LocalModelCompiler()):
        out = tmp_path / compiler.destination
        result = await compiler.compile([shared], out_dir=out)
        assert "Shared with both." in files(out)
        assert result.manifest.withheld == []


@pytest.mark.asyncio
async def test_withholding_does_not_disturb_the_scope_fan_out(tmp_path: Path) -> None:
    """The two filters are independent, and the hard gate stays at 1.0 either way."""
    result = await ClaudeCompiler().compile(
        [
            mem("Ledger uses double-entry.", scope=PROJECT),
            mem("Ledger's private key rotation runbook.", scope=PROJECT,
                locality=local_to(Provider.LOCAL)),
        ],
        out_dir=tmp_path,
    )
    body = files(tmp_path)
    assert "double-entry" in body
    assert "runbook" not in body
    assert result.score.scope_preservation == 1.0
