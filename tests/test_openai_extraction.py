"""The OpenAI extraction path: structured outputs, and failing fast when it cannot run.

No live credits needed. What is pinned here is the behaviour that only shows up at
import scale — a permanent failure must stop the run, not be counted once per turn.
"""

from __future__ import annotations

import pytest

from coletar.extraction.providers import ExtractionConfigurationError, ExtractionUnavailable


class _Status(Exception):
    """Stands in for openai.APIStatusError, which needs a live response to build."""

    def __init__(self, status_code: int, body: object) -> None:
        super().__init__("api error")
        self.status_code = status_code
        self.body = body


def test_quota_exhaustion_is_permanent_and_a_timeout_is_not() -> None:
    """The distinction the importers rely on.

    They catch `ExtractionUnavailable` and continue, and deliberately do not catch
    `ExtractionConfigurationError` — so classifying an unfunded account as the
    former is what turns one billing problem into 17,881 failing API calls.
    """
    from coletar.extraction.openai_provider import _permanent_reason

    quota = _Status(429, {"error": {"code": "credit_balance_exhausted"}})
    assert _permanent_reason(quota, 429) == "credit_balance_exhausted"

    insufficient = _Status(429, {"code": "insufficient_quota"})
    assert _permanent_reason(insufficient, 429) == "insufficient_quota"

    for status in (401, 403):
        assert _permanent_reason(_Status(status, None), status) == f"HTTP {status}"

    # A plain rate limit is transient: the account is fine, the call was too soon.
    throttled = _Status(429, {"error": {"code": "rate_limit_exceeded"}})
    assert _permanent_reason(throttled, 429) is None
    assert _permanent_reason(_Status(500, None), 500) is None


def test_the_proposal_schema_gives_a_model_nowhere_to_put_a_confidence() -> None:
    """§7 enforced by the schema rather than by a parser downstream of it.

    Structured outputs constrain decoding to exactly these fields, so a
    prompt-injected instruction in a mined transcript cannot talk its way into a
    higher confidence, a wider locality, or an existing object's id.
    """
    from coletar.extraction.proposal import Proposal

    fields = set(Proposal.model_json_schema()["$defs"]["ProposedMemory"]["properties"])
    for forbidden in ("confidence", "locality", "id", "scope", "provenance"):
        assert forbidden not in fields, f"a model must not be able to propose {forbidden}"


@pytest.mark.asyncio
async def test_the_api_key_is_read_from_settings_not_only_the_environment(monkeypatch) -> None:
    """pydantic loads `.env` into settings without exporting to `os.environ`, so a
    key sitting in the project's own `.env` was invisible to the SDK."""
    from coletar.config import get_settings

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COLETAR_OPENAI_API_KEY", "sk-test-from-settings")
    get_settings.cache_clear()
    try:
        assert get_settings().openai_api_key == "sk-test-from-settings"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_an_unfunded_account_stops_the_import_instead_of_being_counted(tmp_path) -> None:
    """End to end through the importer: the run raises rather than reporting a
    tidy `unavailable` count for every turn in the archive."""
    import json

    from coletar.acquisition import claude_export
    from coletar.schema.tenancy import tenant_id
    from coletar.store.memory import InMemoryStore

    (tmp_path / "conversations.json").write_text(
        json.dumps(
            [
                {
                    "uuid": "c1",
                    "name": "chat",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "chat_messages": [
                        {
                            "uuid": f"m{i}",
                            "sender": "human",
                            "text": f"I prefer approach number {i}.",
                            "created_at": "2026-01-01T00:00:00Z",
                        }
                        for i in range(25)
                    ],
                }
            ]
        )
    )

    calls = 0

    async def broke(**kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise ExtractionConfigurationError("gpt: credit_balance_exhausted")

    import coletar.extraction as extraction_pkg
    from coletar.config import get_settings

    # The importer resolves `extract_with_model` from the package at call time.
    monkey = pytest.MonkeyPatch()
    monkey.setattr(extraction_pkg, "extract_with_model", broke)
    monkey.setenv("COLETAR_EXTRACTION_MODE", "model")
    get_settings.cache_clear()
    try:
        with pytest.raises(ExtractionConfigurationError):
            await claude_export.import_bundle(
                InMemoryStore(), tenant_id("tenant_quota_test"), tmp_path
            )
        # Bounded by one concurrency window, not by the size of the archive. Calls
        # already in flight when the first failure lands cannot be recalled, so the
        # guarantee is that the run stops after that window rather than repeating a
        # doomed call for all 25 turns — and for a real archive, all 17,881.
        assert calls <= get_settings().extraction_concurrency
        assert calls < 25
    finally:
        monkey.undo()
        get_settings.cache_clear()


def test_unavailable_and_configuration_errors_are_different_types() -> None:
    """Pinned because the importers' behaviour depends entirely on which is raised."""
    assert not issubclass(ExtractionConfigurationError, ExtractionUnavailable)
    assert not issubclass(ExtractionUnavailable, ExtractionConfigurationError)


def test_the_system_prompt_stays_long_enough_to_cache() -> None:
    """OpenAI caches a prompt prefix only from 1024 tokens up.

    At 287 tokens this instruction sat under the line, so every call in a
    17,881-turn import paid full price for a prefix that never changed. Guarded
    because the natural instinct when editing a prompt is to tighten it, and
    tightening this one below the threshold silently multiplies the bill.

    tiktoken is not a runtime dependency, so this counts characters against a
    conservative bytes-per-token ratio rather than tokenising. It is a floor, not
    an estimate: if this passes, the real token count is comfortably higher.
    """
    from coletar.extraction.prompt import CACHEABLE_PREFIX_TOKENS, EXTRACTION_SYSTEM

    # English prose runs ~4 characters per token; 3.5 is a deliberate under-count
    # so this cannot pass on a prompt that would actually fall short.
    floor = int(len(EXTRACTION_SYSTEM) / 4.2)
    assert floor >= CACHEABLE_PREFIX_TOKENS, (
        f"prompt is ~{floor} tokens, under the {CACHEABLE_PREFIX_TOKENS}-token "
        "cacheable prefix — shortening it makes every import call cost full price"
    )


def test_the_prompt_teaches_the_distinction_it_gets_wrong() -> None:
    """The examples are load-bearing, not padding.

    These exact turns were kept as durable preferences by the earlier prompt on
    Chris's own archive. They are in the instruction now because that is what
    fixed them, so removing them is a regression rather than a tidy-up.
    """
    from coletar.extraction.prompt import EXTRACTION_SYSTEM

    for regression in (
        "I run the build site",
        "I use retrieval augmented generation to train gpt-4",
        "we are using QuickSelect",
    ):
        assert regression in EXTRACTION_SYSTEM
    assert "TRANSIENT" in EXTRACTION_SYSTEM and "DURABLE" in EXTRACTION_SYSTEM
