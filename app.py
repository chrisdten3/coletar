"""Vercel's FastAPI entrypoint; local Inspector stays loopback-only."""

import sys
from pathlib import Path

# Vercel installs third-party requirements but does not retain uv's editable
# project link. Resolve this repository's src layout from the deployed bundle.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from coletar.hosted import create_app  # noqa: E402

app = create_app()
