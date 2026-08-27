"""The schema is the product. These tests pin the invariants §2 and §3.1 depend on."""

import pytest
from pydantic import ValidationError

from coletar.schema import (
    ExtractionMethod,
    Memory,
    MemoryKind,
    ObjectType,
    Scope,
    ScopeType,
    default_confidence,
)


def test_ids_are_prefixed_by_type():
    memory = Memory.from_write("Chris uses uv for Python projects.")
    assert memory.id.startswith("mem_")
    assert memory.type is ObjectType.MEMORY


def test_project_scope_requires_an_id():
    with pytest.raises(ValidationError):
        Scope(type=ScopeType.PROJECT)


def test_global_scope_rejects_an_id():
    with pytest.raises(ValidationError):
        Scope(type=ScopeType.GLOBAL, id="proj_1")


def test_connector_writes_outrank_export_parsing():
    """§3.1: a typed tool call is a stronger signal than a parsed export line."""
    assert default_confidence(ExtractionMethod.MCP_LIVE_WRITE) > default_confidence(
        ExtractionMethod.ACCOUNT_EXPORT_PARSE
    )


def test_confidence_defaults_from_extraction_method():
    parsed = Memory.from_write(
        "Prefers dark mode.", extraction_method=ExtractionMethod.ACCOUNT_EXPORT_PARSE
    )
    live = Memory.from_write(
        "Prefers dark mode.", extraction_method=ExtractionMethod.MCP_LIVE_WRITE
    )
    assert parsed.confidence == default_confidence(ExtractionMethod.ACCOUNT_EXPORT_PARSE)
    assert live.confidence > parsed.confidence
    # Provenance carries the same number, so the Inspector and the ranker agree.
    assert live.provenance.confidence == live.confidence


def test_confidence_is_bounded():
    with pytest.raises(ValidationError):
        Memory.from_write("x", confidence=1.5)


def test_touch_bumps_version_and_timestamp():
    memory = Memory.from_write("A goal.", kind=MemoryKind.GOAL)
    before = memory.updated_at
    memory.touch()
    assert memory.version == 2
    assert memory.updated_at >= before
