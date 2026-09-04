"""The tool-call set and the tools it measures must not drift apart.

docs/TOOL_CALLING.md's numbers are only meaningful while the labelled set still
describes the tools that exist. Renaming a tool or dropping a label would leave
the bench scoring a name nothing answers to, silently, and the bench itself is
paid and therefore never runs in CI. This is the cheap guard that does.
"""

from __future__ import annotations

import json

from conftest import FIXTURES

SET = json.loads((FIXTURES / "tool_call_set.json").read_text())
MEASURED_TOOLS = ("search_context", "write_memory")


def test_every_turn_carries_both_labels_and_a_unique_id():
    turns = SET["turns"]
    assert len({turn["id"] for turn in turns}) == len(turns)
    for turn in turns:
        assert isinstance(turn["should_search"], bool), turn["id"]
        assert isinstance(turn["should_write"], bool), turn["id"]
        assert turn["user"].strip(), turn["id"]


def test_the_set_is_not_lopsided_enough_to_be_scored_by_guessing():
    """A set that is nearly all positives is passed by a model that always fires,
    which is the exact behaviour this measurement exists to detect."""
    turns = SET["turns"]
    for label in ("should_search", "should_write"):
        positives = sum(turn[label] for turn in turns)
        assert 3 <= positives <= len(turns) - 3, f"{label}: {positives}/{len(turns)}"


def test_the_definition_and_preamble_survive():
    assert SET["_definition"], "the written definition is what makes the labels checkable"
    assert [m["role"] for m in SET["_preamble"]] == ["user", "assistant", "user", "assistant"]


async def test_the_tools_the_set_measures_still_exist():
    from coletar.mcp.server import mcp

    registered = {tool.name for tool in await mcp.list_tools()}
    assert set(MEASURED_TOOLS) <= registered


async def test_the_measured_tools_still_describe_themselves_to_a_model():
    """docs/TOOL_CALLING.md attributes the over-firing to description prose. An
    empty description would change the finding without changing the numbers."""
    from coletar.mcp.server import mcp

    for tool in await mcp.list_tools():
        if tool.name in MEASURED_TOOLS:
            assert tool.description and len(tool.description) > 100, tool.name
