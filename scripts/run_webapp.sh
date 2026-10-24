#!/usr/bin/env bash
# Start the ForeSmoke review webapp at http://localhost:8000 using the repo's .venv.
# Creates the virtual environment and installs dependencies on first use.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "Creating .venv (first run only)…"
  python3 -m venv .venv
fi
if ! "$PY" -c "import fastapi, uvicorn, multipart, lap, cv2, ultralytics, shapely, foresmoke" >/dev/null 2>&1; then
  echo "Installing ForeSmoke with perception, reasoning and webapp extras (first run only)…"
  "$PY" -m pip install --upgrade pip >/dev/null
  "$PY" -m pip install -e '.[perception,reasoning,webapp,dev]'
fi
exec "$PY" -m foresmoke.webapp "$@"
