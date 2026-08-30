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
    # Bearer keys, comma-separated, as `id:secret` or `id:secret:read|write`.
    # Empty means the server refuses to start -- it never serves unauthenticated.
    mcp_api_keys: str = ""

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
