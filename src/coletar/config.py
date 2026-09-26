"""Runtime configuration. Everything is env-driven so the same code runs as the
local proxy daemon, the hosted MCP server, or a one-off CLI compile."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COLETAR_", env_file=".env", extra="ignore")

    # Tenant resolved by *application boundaries* only — the CLI and the local
    # proxy. The MCP server never reads this: it derives the tenant from the
    # authenticated principal, because a connector falling back to a configured
    # tenant is a connector serving someone else's graph.
    default_tenant_id: str = "tenant_local"

    # Canonical store. "memory" runs the full stack with no Postgres (tests, demos).
    store_backend: Literal["postgres", "memory"] = "memory"
    database_url: str = "postgresql://coletar:coletar@localhost:5433/coletar"
    # Snapshot file for the "memory" backend, so consecutive CLI runs and a
    # separately-launched proxy see the same graph. Development convenience only.
    store_path: Path = Path("data/coletar.json")

    # Local proxy daemon (§4 local-model leg).
    upstream_base_url: str = "http://localhost:11434/v1"
    upstream_api_key: str = ""
    proxy_port: int = 8787

    # Hosted MCP server (§9). ChatGPT only accepts remote HTTPS servers, so this
    # is always an HTTP transport, never stdio, outside of local development.
    mcp_port: int = 8788
    # Loopback by default. A container sets 0.0.0.0 explicitly, and binding a public
    # interface is gated on a real backend — see `coletar.mcp.server.run`.
    mcp_host: str = "127.0.0.1"
    # Public hostnames this service answers on, comma-separated. The MCP SDK enforces
    # DNS-rebinding protection and trusts only localhost by default, so a deployment
    # behind a real domain must name itself here or every request is refused with
    # 421 Misdirected Request — after passing authentication, which makes it look
    # like anything but a host check.
    mcp_allowed_hosts: str = ""
    # Bearer keys, comma-separated, as `id:secret` or `id:secret:read|write`.
    # Empty means the server refuses to start -- it never serves unauthenticated.
    mcp_api_keys: str = ""

    # M4.2: when set, the local proxy reaches the graph as an MCP client instead
    # of opening the database itself. Unset keeps the zero-infrastructure default,
    # which is what makes the wedge work before anything is deployed.
    mcp_url: str = ""
    mcp_api_key: str = ""
    # Origins the browser bridge may call from. An allowlist, never a wildcard: these
    # endpoints are authenticated, and a wildcard would let any page a user visits
    # attempt to spend their token.
    cors_allow_origins: str = "https://claude.ai,https://chatgpt.com,https://chat.openai.com"

    # Read-only Context Inspector (§8.2). Local-only, so no auth of its own.
    inspector_port: int = 8789
    # The hosted deployment serves its workspace unauthenticated by choice; see
    # `coletar.hosted`. Connector credentials below are a separate authority and are
    # unaffected.
    public_url: str = ""
    # Which identity provider vouches for an account. "local" trusts whoever can
    # reach the port, which is a laptop assumption and is refused when `public_url`
    # is set. Clerk and Supabase Auth plug in here; see `coletar.accounts.identity`
    # for the whole of what adopting one involves.
    identity_provider: str = "local"

    # --- Clerk ------------------------------------------------------------
    # The issuer URL Clerk prints on the API Keys screen, e.g.
    # https://your-app.clerk.accounts.dev. Root of the JWKS URL and the `iss`
    # every session token carries.
    clerk_issuer: str = ""
    # Only set to override the derived `{issuer}/.well-known/jwks.json`.
    clerk_jwks_url: str = ""
    # Origins allowed to present a Clerk token here, comma-separated. Empty is
    # refused rather than treated as "any": a token minted for another site on the
    # same Clerk instance must not be spendable here.
    clerk_authorized_parties: str = ""
    # Publishable, and therefore genuinely safe in the browser bundle -- it is the
    # one Clerk value that is meant to be public. No secret key is read anywhere in
    # coletar: verification is a signature check against a public JWKS, so the
    # backend never needs Clerk's secret at all.
    clerk_publishable_key: str = ""

    # --- Supabase Auth ----------------------------------------------------
    # The project URL, e.g. https://your-ref.supabase.co. Root of both the `iss`
    # every access token carries and the JWKS this server verifies against, which
    # are derived from it together so a partial edit cannot point the issuer check
    # and the trusted keys at two different projects.
    supabase_url: str = ""
    # Only set to override the derived `{url}/auth/v1/.well-known/jwks.json`.
    supabase_jwks_url: str = ""
    # The anon key, which is publishable and belongs in the browser bundle -- it is
    # what the Supabase JS client presents to reach the auth endpoints, and it
    # grants nothing on its own beyond what RLS allows (migrations 008 and 012
    # enable RLS with no client policies, so it reaches no coletar table at all).
    #
    # The *service role* key is deliberately not a setting. It bypasses RLS and can
    # mint sessions for any user, and no request path in coletar needs it:
    # verification is a signature check against a public JWKS. Provisioning demo
    # users needs it and reads it from the environment in that script alone.
    supabase_anon_key: str = ""

    # Invite-gated beta. Comma-separated email addresses that may provision a new
    # account; an empty list means *closed*, not open. Someone who signs in through
    # Clerk without being listed gets a clear "not yet" rather than a new empty
    # workspace. Existing accounts are unaffected -- this gates provisioning, not
    # sign-in, so removing an address never locks its owner out of their own graph.
    invite_allowlist: str = ""
    # Set true to open provisioning to anyone Clerk authenticates. This is the
    # switch from invite-only beta to public signup, and it is deliberately one
    # explicit setting rather than "leave the allowlist empty".
    open_registration: bool = False
    cron_secret: str = Field(default="", validation_alias="CRON_SECRET")

    # Retrieval. "hashing" is the default because the in-process store has to work
    # with nothing installed; "ollama" is what a real deployment runs, against the
    # user's own model server where inference is free (§4, §11).
    embedding_backend: Literal["hashing", "ollama"] = "hashing"
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768
    retrieval_token_budget: int = 1500
    retrieval_top_k: int = 12
    # Below this blended score, a hit is not returned at all — "nothing relevant"
    # beats the five least-bad rows, which get rendered into a prompt under the
    # heading "Known context about this user".
    #
    # The right value depends on the embedder, which is why this is a setting and
    # not a constant. Embedding models have a high baseline similarity: measured on
    # a 3,818-object corpus with `nomic-embed-text`, nonsense queries topped out at
    # 0.433 while real ones bottomed out at 0.505, so 0.45 sits in the gap. The
    # `hashing` backend scores far lower — correct lexical-only hits land near 0.26
    # — and the same 0.45 would return nothing at all.
    #
    # **Off by default, and that is deliberate.** A floor of 0.15 was tried and it
    # broke four of the published baselines in `tests/test_retrieval_eval.py`: with
    # the `hashing` backend, genuinely correct hits routinely score below it. A
    # measured, published number must not be invalidated by a constant somebody
    # guessed, so the default changes nothing and raising it is an explicit,
    # per-deployment decision.
    #
    # Recommended with `nomic-embed-text`: **0.45**. Measured above, and confirmed
    # to leave the baselines alone because they run on `hashing`. See
    # docs/RETRIEVAL.md before changing it.
    retrieval_min_score: float = 0.0

    # Which §5.1 reranking strategy `retrieve` uses when a caller names none.
    #
    # "published" is `rank_score`'s own order and is what every published baseline
    # was measured with — the default for that reason, since a strategy that
    # changed results by existing would make those numbers ambiguous.
    #
    # "mmr" trades a little relevance for coverage. It is the cheap win on a corpus
    # with restatements: the real one answers "where do I work" with "I work at
    # Eppley" twice and "i like chelsea" with "A club." twice, and each duplicate
    # costs tokens while adding nothing.
    #
    # "model" is the cross-encoder prototype in `strategy.py`, run against the
    # user's own model server. It exists to measure whether joint (query, document)
    # scoring is worth a real cross-encoder's dependency weight; it is not a
    # recommended production setting yet.
    retrieval_reranker: Literal["published", "mmr", "model"] = "published"
    #: MMR's relevance/diversity balance. 1.0 reproduces "published" exactly.
    retrieval_mmr_lambda: float = 0.7
    #: Which local model scores pairs when `retrieval_reranker` is "model".
    retrieval_reranker_model: str = "llama3.1"

    # Whether imports use the deterministic pattern recogniser or model-assisted
    # extraction. This must be separate from provider selection: the old code made
    # `ollama` mean "heuristic", even though Ollama is itself a model provider.
    extraction_mode: Literal["heuristic", "model"] = "heuristic"

    # Selecting a provider is a data-handling decision. The local default keeps user
    # turns on their machine; third-party providers remain explicit opt-ins.
    extraction_provider: Literal["ollama", "anthropic", "openai"] = "ollama"
    #: How many extraction calls are in flight at once during an import. Only the
    #: calls are parallel; graph writes stay ordered. 8 keeps a real archive under
    #: an hour without tripping a provider's rate limit.
    extraction_concurrency: int = 8

    # Provider-specific defaults are intentionally visible rather than one ambiguous
    # `extraction_model` whose meaning changes with another setting.
    ollama_extraction_model: str = "llama3.1"
    anthropic_extraction_model: str = "claude-sonnet-5"
    openai_extraction_model: str = "gpt-5.6-terra"
    #: Accepts the vendor's own name as well as the prefixed one, because that is
    #: what a `.env` already holds and what every other tool on the machine reads.
    #: Pydantic loads `.env` into settings without exporting to `os.environ`, so a
    #: key sitting there was invisible to the OpenAI client until this existed.
    openai_api_key: str = Field(
        default="", validation_alias=AliasChoices("COLETAR_OPENAI_API_KEY", "OPENAI_API_KEY")
    )

    # Capture-then-batch (docs/CAPTURE_AND_BATCH.md). Off by default: retaining the
    # turns a user typed before anything has judged them is a materially larger
    # commitment than storing extracted memories. Encryption and effective expiry
    # reduce the consequence; they do not replace informed opt-in.
    capture_turns: bool = False

    # `off` means explicit `write_memory` calls only. The heuristic remains an
    # explicit compatibility mode for installations that decline raw-turn retention;
    # collect-then-batch queues the encrypted turn and writes no regex memory.
    live_extraction_mode: Literal["off", "heuristic", "collect_then_batch"] = "off"

    # How long a captured turn is kept. `coletar expire` retires its graph object and
    # destroys its per-object key, leaving only unreadable ciphertext and provenance.
    # 30 days is a placeholder for a product decision, not a researched figure.
    capture_ttl_days: int = 30

    # Answering a history question about a subject sends that subject's stored
    # memories to a model (AGENTS.md §1, amended 2026-09-26). Deliberately a
    # separate setting from `extraction_provider`: "mine my conversations with a
    # third party" and "show my accepted memories to a third party" are two
    # consents, and one must not silently grant the other. `none` keeps the
    # deterministic analysis and sends nothing anywhere -- it is what the demo
    # runs on, and it is a complete feature rather than a degraded one.
    history_thread_provider: Literal["none", "ollama", "anthropic", "openai"] = "none"
    ollama_thread_model: str = "llama3.1"
    anthropic_thread_model: str = "claude-sonnet-5"
    openai_thread_model: str = "gpt-5.6-terra"
    #: Ceiling on how many objects one thread question may send to that backend.
    #: The boundary says "one subject's objects, never the whole graph"; this is
    #: the number that makes it true rather than aspirational.
    history_thread_max_objects: int = 120

    extraction_batch_size: int = 100

    # Scheduled batch worker. The interval is a latency choice, not a throughput
    # one: it decides how long after typing a turn the user can expect to see a
    # memory from it.
    worker_interval_seconds: float = 300.0
    # Longer than a pass can reasonably take, because the cost of a too-short TTL
    # is two workers on one episode, while the cost of a too-long one is only queue
    # latency after a crash. `extraction_batch_size` turns against a slow provider
    # is the number to size this against.
    worker_lease_ttl_seconds: float = 900.0

    # Queue health thresholds. Both answer "is capture still reaching the graph",
    # which is invisible from the outside: a stalled queue and a quiet user look
    # identical until someone asks how old the oldest pending turn is.
    queue_alert_pending_hours: float = 6.0
    queue_alert_failures: int = 5

    # M7: per-principal rate limit on the hosted surfaces. Keyed by credential,
    # not by IP — an office NAT is not one caller and a rotating client is not
    # several.
    rate_limit_per_minute: int = 120
    rate_limit_burst: int = 30

    # Where an API-triggered compile writes. Server-side on purpose: a compile
    # hands context to another company, so the package should be something a
    # human fetched deliberately rather than a response body.
    compile_output_dir: str = "build/api-compile"

    # M7 webhooks. Deliveries carry event metadata only — never object content —
    # so a leaked URL leaks that something changed, not what it said.
    webhooks: str = ""
    webhooks_allow_private: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
