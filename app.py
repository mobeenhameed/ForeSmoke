"""Vercel ASGI entry point for the ForeSmoke review webapp."""
from __future__ import annotations

import os

# Vercel functions have a read-only deployment bundle and an ephemeral /tmp directory.
os.environ.setdefault("FORESMOKE_WEBAPP_DATA", "/tmp/foresmoke/webapp_data")
os.environ.setdefault("FORESMOKE_RUNS_DIR", "/tmp/foresmoke/runs")

from foresmoke.webapp.server import create_app  # noqa: E402

app = create_app(seed_sample=False)
