#!/usr/bin/env bash
# Helper script to run the lean ASR-only service with auto model download, optional debug,
# and safe port re-use. It will:
#  1. Create (or reuse) .venv-clean
#  2. Install minimal runtime dependencies (from requirements.txt)
#  3. Kill an existing uvicorn process bound to the chosen port (default 8100) if any
#  4. Start uvicorn for service.main:app with reload disabled by default
#
# Usage:
#   ./run_lean.sh                # start on port 8100
#   PORT=9000 ./run_lean.sh      # custom port
#   STREAM_DEBUG=true ./run_lean.sh  # enable debug JSON events
#   RELOAD=1 ./run_lean.sh       # enable --reload for development
#
# Environment variables you can set before running:
#   PORT           (default 8100)
#   STREAM_DEBUG   (default empty / off)
#   MODEL_WHISPER  (maps to STREAM_MODEL_WHISPER; default small-int8)
#   SAVE_DIR       (maps to STREAM_SAVE_DIR; where to persist transcripts)
#   RELOAD         (if set to 1 enables auto reload)
#
set -euo pipefail

PORT="${PORT:-8100}"
RELOAD="${RELOAD:-0}"
MODEL_WHISPER="${MODEL_WHISPER:-small-int8}"
SAVE_DIR="${SAVE_DIR:-stream_out}"

# Export variables expected by settings
export STREAM_MODEL_WHISPER="$MODEL_WHISPER"
export STREAM_SAVE_DIR="$SAVE_DIR"

if [ ! -d .venv-clean ]; then
  echo "[setup] Creating clean venv (.venv-clean)"
  python -m venv .venv-clean
fi
source .venv-clean/bin/activate

# Install dependencies only if faster-whisper not present (quick heuristic)
if ! python -c 'import faster_whisper' 2>/dev/null; then
  echo "[setup] Installing dependencies (requirements.txt)"
  pip install -q -r requirements.txt || { echo "[error] dependency install failed"; exit 1; }
fi

# Ensure save dir exists
mkdir -p "$SAVE_DIR"

# Find process using PORT (Linux) and terminate if it's uvicorn/python
EXISTING_PID="$(lsof -t -i TCP:$PORT || true)"
if [ -n "$EXISTING_PID" ]; then
  echo "[info] Port $PORT in use by PID(s): $EXISTING_PID"
  # Check command name
  for PID in $EXISTING_PID; do
    CMD="$(ps -o comm= -p "$PID" || true)"
    if echo "$CMD" | grep -Eq "python|uvicorn"; then
      echo "[action] Killing existing server process $PID"
      kill "$PID" || true
      # Wait briefly
      for i in {1..10}; do
        if kill -0 "$PID" 2>/dev/null; then sleep 0.2; else break; fi
      done
      if kill -0 "$PID" 2>/dev/null; then
        echo "[warn] Force killing $PID"
        kill -9 "$PID" || true
      fi
    else
      echo "[warn] Port $PORT used by non-uvicorn process ($CMD); not killing. Choose a different PORT."
      exit 1
    fi
  done
fi

UVICORN_ARGS=("service.main:app" "--host" "0.0.0.0" "--port" "$PORT" "--log-level" "info")
if [ "$RELOAD" = "1" ]; then
  UVICORN_ARGS+=("--reload")
fi

if [ -n "${STREAM_DEBUG:-}" ]; then
  echo "[run] Starting lean service with debug on port $PORT (model=$MODEL_WHISPER)"
else
  echo "[run] Starting lean service on port $PORT (model=$MODEL_WHISPER)"
fi

# Persist pid
python - <<PY
import os, sys
with open('lean.pid','w') as f: f.write(str(os.getpid()))
PY

exec python -m uvicorn "${UVICORN_ARGS[@]}"