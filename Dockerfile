# coletar MCP service (M3.3).
#
# The image runs the *MCP server* only. The local proxy is a daemon that sits in
# front of a model on the user's own machine, so deploying it would be meaningless —
# see docs/CONNECTORS.md.
#
# Multi-stage so the runtime image carries no build tooling and no lockfile
# resolution: dependencies are installed from uv.lock, frozen, so a deploy installs
# exactly what the test suite ran against.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, in their own layer: they change far less often than the source,
# so an ordinary code change reuses this layer rather than re-resolving the world.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
RUN uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm AS runtime

# Non-root. Nothing in the service writes to disk — the graph lives in Postgres — so
# the filesystem can stay owned by someone else entirely.
RUN useradd --create-home --uid 10001 coletar

WORKDIR /app
COPY --from=builder --chown=coletar:coletar /app/.venv /app/.venv
COPY --from=builder --chown=coletar:coletar /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Bind the container's interface. `run()` refuses this unless the store backend
    # is Postgres, so a misconfigured deploy fails at boot rather than silently
    # serving a graph that disappears on the next restart.
    COLETAR_MCP_HOST=0.0.0.0 \
    COLETAR_MCP_PORT=8788 \
    COLETAR_STORE_BACKEND=postgres

USER coletar
EXPOSE 8788

# No secrets baked in: COLETAR_DATABASE_URL and COLETAR_MCP_API_KEYS arrive from the
# platform's secret store. The server fails closed without the latter.
CMD ["coletar", "serve-mcp"]
