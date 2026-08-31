"""Runtime configuration. Everything is env-driven so the same code runs as the
local proxy daemon, the hosted MCP server, or a one-off CLI compile."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COLETAR_", env_file=".env", extra="ignore"
    )

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

    # Retrieval. "hashing" is the default because the in-process store has to work
    # with nothing installed; "ollama" is what a real deployment runs, against the
    # user's own model server where inference is free (§4, §11).
    embedding_backend: Literal["hashing", "ollama"] = "hashing"
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768
    retrieval_token_budget: int = 1500
    retrieval_top_k: int = 12

    # M6.2: which local model does model-assisted extraction. Runs against the
    # user's own server, where inference is free — §11 names the cost of doing it
    # any other way at consumer scale.
    extraction_model: str = "llama3.1"

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
