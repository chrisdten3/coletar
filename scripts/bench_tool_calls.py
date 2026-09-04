"""Does a model call coletar's tools when nobody tells it to?

Every connector in docs/CONNECTORS.md rests on an assumption nothing has ever
measured: that a model handed these four tools will *choose* to call them at the
right moments. If it never calls `search_context` unprompted, Live Sync degrades
to a manual lookup the user has to ask for by name, and the onboarding has to
teach that. So this measures the decision, not the plumbing.

It calls a paid API. A spec is `provider:model`:

    uv run python scripts/bench_tool_calls.py openai:gpt-5.6-terra
    uv run python scripts/bench_tool_calls.py anthropic:claude-sonnet-5

**What this is a proxy for.** The real clients are Claude Desktop, the claude.ai
Custom Connector and ChatGPT's Developer Mode — none of which can be driven here,
because driving a provider's UI is prohibited outright (AGENTS.md constraint 2)
and the connector is not deployed anyway. What is measurable is the same tool
schemas, pulled live from `coletar.mcp.server`, presented to the same model
family through the API. The client's own system prompt is not ours and is not
reproduced, so treat these numbers as the *decision quality of the tool
descriptions*, not as an end-to-end connector measurement.

Three conditions, because the interesting failure is not the same in each:

  warm    the turn arrives four messages into an unrelated chat, with the MCP
          server's `instructions` in the system prompt. This is production.
  cold    the turn is the first message. The description says "call at the start
          of a conversation", so this should be the easy case; if it is not, the
          rest does not matter.
  bare    warm, but with no server instructions at all — only the tool
          descriptions. Isolates how much work that prose is actually doing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tool_call_set.json"
CONDITIONS = ("warm", "cold", "bare")
SEARCH_TOOL = "search_context"
WRITE_TOOL = "write_memory"


@dataclass(frozen=True)
class Decision:
    """What one model did with one turn, over two rounds.

    One round is not enough to ask whether the model writes. A model that searches
    first can only reach `write_memory` after that result comes back, so a
    single-shot harness would report "it never writes" when what it saw was "it
    searched first". Round two feeds an empty tool result back — empty so the
    measurement is of the model's own judgement about the turn, not of whatever
    content we chose to inject — and records what it does next.
    """

    turn_id: str
    condition: str
    first: tuple[str, ...]
    second: tuple[str, ...]
    error: str | None = None

    @property
    def called(self) -> tuple[str, ...]:
        """Every tool reached for anywhere in the turn."""
        return tuple(dict.fromkeys(self.first + self.second))


@dataclass
class Score:
    provider: str
    model: str
    condition: str
    tool: str
    #: "first" counts only the model's opening move; "turn" counts the whole turn,
    #: including what it did after an empty tool result came back.
    stage: str
    turns: int
    fired_when_it_should: int
    should: int
    fired_when_it_should_not: int
    should_not: int
    errors: int
    fixture_sha256: str
    schema_sha256: str
    measured_at: str

    @property
    def recall(self) -> float:
        return self.fired_when_it_should / self.should if self.should else 0.0

    @property
    def false_fire_rate(self) -> float:
        return self.fired_when_it_should_not / self.should_not if self.should_not else 0.0

    @property
    def precision(self) -> float:
        fired = self.fired_when_it_should + self.fired_when_it_should_not
        return self.fired_when_it_should / fired if fired else 0.0


async def _tool_schemas() -> tuple[list[dict[str, Any]], str]:
    """The real tool definitions, not a copy of them.

    A hand-written duplicate would measure a description that no client ever sees,
    and would keep scoring well after someone edited the real one.
    """
    from coletar.mcp.server import mcp

    tools = [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.input_schema,
        }
        for tool in sorted(await mcp.list_tools(), key=lambda t: t.name)
    ]
    digest = hashlib.sha256(
        json.dumps(tools, sort_keys=True).encode()
        + (mcp.instructions or "").encode()
    ).hexdigest()
    return tools, digest


def _instructions() -> str:
    from coletar.mcp.server import mcp

    return mcp.instructions or ""


def _messages(turn: str, condition: str, preamble: list[dict[str, str]]) -> list[dict[str, str]]:
    prior = [] if condition == "cold" else preamble
    return [*prior, {"role": "user", "content": turn}]


#: What a tool "returns" in round two. Deliberately empty: a populated result
#: would measure whether the model reacts to content we wrote, not whether it
#: judged the turn to need the tool.
EMPTY_RESULT = json.dumps({"results": [], "note": "no matching memories"})


async def _ask_openai(
    *, model: str, tools: list[dict[str, Any]], system: str, messages: list[dict[str, str]]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    from openai import AsyncOpenAI

    payload = [
        {
            "type": "function",
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
            "strict": False,
        }
        for tool in tools
    ]
    conversation: list[Any] = [
        *([{"role": "system", "content": system}] if system else []),
        *messages,
    ]

    client = AsyncOpenAI(max_retries=5)
    try:
        first = await client.responses.create(
            model=model, input=conversation, tools=payload, tool_choice="auto", store=False
        )
        calls = [item for item in first.output if getattr(item, "type", "") == "function_call"]
        if not calls:
            return (), ()

        # Only the calls go back, rebuilt minimally: `model_dump()` carries fields
        # such as `status` that the API accepts on output but rejects on input.
        conversation.extend(
            {
                "type": "function_call",
                "call_id": call.call_id,
                "name": call.name,
                "arguments": call.arguments,
            }
            for call in calls
        )
        conversation.extend(
            {"type": "function_call_output", "call_id": call.call_id, "output": EMPTY_RESULT}
            for call in calls
        )
        second = await client.responses.create(
            model=model, input=conversation, tools=payload, tool_choice="auto", store=False
        )
    finally:
        await client.close()

    return (
        tuple(call.name for call in calls),
        tuple(
            item.name for item in second.output if getattr(item, "type", "") == "function_call"
        ),
    )


async def _ask_anthropic(
    *, model: str, tools: list[dict[str, Any]], system: str, messages: list[dict[str, str]]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    from anthropic import AsyncAnthropic

    payload = [
        {
            "name": tool["name"],
            "description": tool["description"],
            "input_schema": tool["input_schema"],
        }
        for tool in tools
    ]
    conversation: list[Any] = list(messages)

    client = AsyncAnthropic(max_retries=5)
    try:
        first = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=system or "",
            tools=payload,  # type: ignore[arg-type]
            messages=conversation,
        )
        calls = [block for block in first.content if block.type == "tool_use"]
        if not calls:
            return (), ()

        conversation.append({"role": "assistant", "content": first.content})
        conversation.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": call.id, "content": EMPTY_RESULT}
                    for call in calls
                ],
            }
        )
        second = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=system or "",
            tools=payload,  # type: ignore[arg-type]
            messages=conversation,
        )
    finally:
        await client.close()

    return (
        tuple(call.name for call in calls),
        tuple(block.name for block in second.content if block.type == "tool_use"),
    )


async def _run_one(
    *,
    provider: str,
    model: str,
    tools: list[dict[str, Any]],
    system: str,
    turn: dict[str, Any],
    condition: str,
    preamble: list[dict[str, str]],
    limit: asyncio.Semaphore,
) -> Decision:
    ask = _ask_openai if provider == "openai" else _ask_anthropic
    async with limit:
        try:
            called = await ask(
                model=model,
                tools=tools,
                system=system,
                messages=_messages(turn["user"], condition, preamble),
            )
        except Exception as exc:  # noqa: BLE001 - one bad turn must not lose the run
            return Decision(turn["id"], condition, (), (), f"{exc.__class__.__name__}: {exc}")
    return Decision(turn["id"], condition, called[0], called[1])


def _score(
    *,
    provider: str,
    model: str,
    condition: str,
    tool: str,
    stage: str,
    label: str,
    turns: list[dict[str, Any]],
    decisions: dict[str, Decision],
    fixture_sha: str,
    schema_sha: str,
) -> Score:
    by_id = {t["id"]: t for t in turns}
    hit = miss = spurious = quiet = errors = 0
    for turn_id, decision in decisions.items():
        if decision.error:
            errors += 1
            continue
        fired = tool in (decision.first if stage == "first" else decision.called)
        if by_id[turn_id][label]:
            hit += fired
            miss += not fired
        else:
            spurious += fired
            quiet += not fired
    return Score(
        provider=provider,
        model=model,
        condition=condition,
        tool=tool,
        stage=stage,
        turns=len(decisions),
        fired_when_it_should=hit,
        should=hit + miss,
        fired_when_it_should_not=spurious,
        should_not=spurious + quiet,
        errors=errors,
        fixture_sha256=fixture_sha,
        schema_sha256=schema_sha,
        measured_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


async def main(specs: list[str]) -> int:
    raw = FIXTURE.read_bytes()
    fixture_sha = hashlib.sha256(raw).hexdigest()
    data = json.loads(raw)
    turns: list[dict[str, Any]] = data["turns"]
    # A smoke run against a couple of turns costs cents and catches a wrong
    # request shape before the full set does.
    if (cap := os.environ.get("BENCH_LIMIT")) is not None:
        turns = turns[: int(cap)]
    preamble: list[dict[str, str]] = data["_preamble"]

    tools, schema_sha = await _tool_schemas()
    instructions = _instructions()
    conditions = os.environ.get("BENCH_CONDITIONS", ",".join(CONDITIONS)).split(",")
    limit = asyncio.Semaphore(int(os.environ.get("BENCH_CONCURRENCY", "4")))

    all_scores: list[Score] = []
    all_decisions: list[Decision] = []
    for spec in specs:
        provider, _, model = spec.partition(":")
        if provider not in {"openai", "anthropic"}:
            print(f"unknown provider {provider!r}; use openai: or anthropic:", file=sys.stderr)
            return 2
        for condition in conditions:
            system = "" if condition == "bare" else instructions
            results = await asyncio.gather(
                *(
                    _run_one(
                        provider=provider,
                        model=model,
                        tools=tools,
                        system=system,
                        turn=turn,
                        condition=condition,
                        preamble=preamble,
                        limit=limit,
                    )
                    for turn in turns
                )
            )
            all_decisions.extend(results)
            decisions = {d.turn_id: d for d in results}
            for tool, label in ((SEARCH_TOOL, "should_search"), (WRITE_TOOL, "should_write")):
                for stage in ("first", "turn"):
                    all_scores.append(
                        _score(
                            provider=provider,
                            model=model,
                            condition=condition,
                            tool=tool,
                            stage=stage,
                            label=label,
                            turns=turns,
                            decisions=decisions,
                            fixture_sha=fixture_sha,
                            schema_sha=schema_sha,
                        )
                    )

    header = (
        f"{'model':<20} {'cond':<6} {'tool':<15} {'stage':<6} "
        f"{'recall':>8} {'false-fire':>11} {'prec':>7} {'err':>4}"
    )
    print(header)
    print("-" * len(header))
    for score in all_scores:
        print(
            f"{score.model:<20} {score.condition:<6} {score.tool:<15} {score.stage:<6} "
            f"{score.recall:>7.1%} {score.false_fire_rate:>11.1%} "
            f"{score.precision:>7.1%} {score.errors:>4}"
        )

    out = Path(os.environ.get("BENCH_OUT", "tool_call_bench.json"))
    out.write_text(
        json.dumps(
            {
                "scores": [asdict(s) | {"recall": s.recall, "false_fire_rate": s.false_fire_rate}
                           for s in all_scores],
                "decisions": [asdict(d) for d in all_decisions],
            },
            indent=2,
        )
    )
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
