#!/usr/bin/env bash
set -euo pipefail

if [ ! -d .venv ]; then
  python -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt pytest httpx >/dev/null

echo "[1/3] Running smoke latency test"
pytest tests/stream/test_latency_harness.py -q || { echo "Latency harness failed"; exit 1; }

echo "[2/3] Starting server (Ctrl+C to stop)"
echo "Open http://localhost:8000 and connect a WebSocket client to /ws"
uvicorn app:app --host 0.0.0.0 --port 8000
