"""Vercel ASGI entry point for the ForeSmoke review webapp."""
from __future__ import annotations

import os
from pathlib import Path
import shutil

# Vercel functions have a read-only deployment bundle and an ephemeral /tmp directory.
os.environ.setdefault("FORESMOKE_WEBAPP_DATA", "/tmp/foresmoke/webapp_data")
os.environ.setdefault("FORESMOKE_RUNS_DIR", "/tmp/foresmoke/runs")


def _seed_precomputed_results() -> None:
    """Populate writable serverless storage with locally generated results."""
    root = Path(__file__).resolve().parent
    copies = (
        (root / "webapp_data" / "runs", Path(os.environ["FORESMOKE_WEBAPP_DATA"]) / "runs"),
        (root / "webapp_data" / "zones", Path(os.environ["FORESMOKE_WEBAPP_DATA"]) / "zones"),
        (root / "runs", Path(os.environ["FORESMOKE_RUNS_DIR"])),
    )
    for source, destination in copies:
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)


_seed_precomputed_results()

from foresmoke.webapp.server import create_app  # noqa: E402

app = create_app(seed_sample=False)
