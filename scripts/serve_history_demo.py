"""Serve the inspector against a throwaway in-memory graph with a full history.

The History page is the one surface that cannot be demoed against an empty
workspace: every chart on it is a shape over months of events, and a fresh store
draws six flat lines. This seeds one persona -- including the import burst, the
topic backfill and the retrieval traces -- into an in-memory store and serves it,
so the page can be looked at without touching Postgres or the real corpus.

    uv run python scripts/serve_history_demo.py [persona] [port]

Nothing here is a fixture for tests. `tests/test_history.py` builds its own
graphs; this exists to be looked at.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PERSONA = sys.argv[1] if len(sys.argv) > 1 else "engineer"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8795

# A fresh file every run: `build_persona` mints new ids on every call, so
# re-seeding a store that already has objects gives two of everything.
store_path = Path(tempfile.gettempdir()) / f"coleta-history-demo-{PERSONA}.json"
store_path.unlink(missing_ok=True)

os.environ["COLETAR_STORE_BACKEND"] = "memory"
os.environ["COLETAR_STORE_PATH"] = str(store_path)
os.environ["COLETAR_DEFAULT_TENANT_ID"] = "tenant_demo"
os.environ["COLETAR_IDENTITY_PROVIDER"] = "local"

from coletar.demo import PERSONAS_BY_KEY, build_persona  # noqa: E402
from coletar.schema.tenancy import TenantId  # noqa: E402
from coletar.store import build_store  # noqa: E402


async def seed() -> None:
    store = build_store()
    tenant = TenantId("tenant_demo")
    persona = PERSONAS_BY_KEY[PERSONA]
    await build_persona(store, tenant, persona)
    events = await store.list_events(tenant, limit=200_000)
    objects = await store.list_objects(
        tenant, limit=5000, include_retired=True, include_superseded=True
    )
    span = max(e.at for e in events) - min(e.at for e in events)
    print(
        f"{persona.name}: {len(objects)} objects, {len(events)} events "
        f"over {span.days} days -> http://127.0.0.1:{PORT}/app#/audit",
        flush=True,
    )


asyncio.run(seed())

import uvicorn  # noqa: E402

from coletar.inspector.app import app  # noqa: E402

uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
