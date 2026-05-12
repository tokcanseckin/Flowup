#!/usr/bin/env bash
# FlowUp – start backend + frontend
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
BACKEND_PORT=8000
FRONTEND_PORT=5173
BACKEND_LOG=/tmp/flowup_backend.log
FRONTEND_LOG=/tmp/flowup_frontend.log

# ── helpers ──────────────────────────────────────────────────────────────────
kill_port() {
  lsof -ti tcp:"$1" | xargs kill -9 2>/dev/null || true
}

wait_for() {
  local url="$1" label="$2" tries=0
  while ! curl -sf "$url" >/dev/null 2>&1; do
    tries=$((tries+1))
    if [ $tries -ge 60 ]; then
      echo "ERROR: $label did not start (timeout). Check logs above."
      return 1
    fi
    sleep 0.5
  done
}

# ── stop existing processes ───────────────────────────────────────────────────
echo "Stopping any existing processes on ports $BACKEND_PORT / $FRONTEND_PORT …"
kill_port $BACKEND_PORT
kill_port $FRONTEND_PORT
sleep 0.5

# ── backend ───────────────────────────────────────────────────────────────────
echo "Starting backend …  (log: $BACKEND_LOG)"
cd "$BACKEND"
"$VENV/bin/uvicorn" main:app --port $BACKEND_PORT > "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

wait_for "http://127.0.0.1:$BACKEND_PORT/api/health" "Backend"
echo "  Backend ready (pid $BACKEND_PID)"

# ── frontend ──────────────────────────────────────────────────────────────────
echo "Starting frontend … (log: $FRONTEND_LOG)"
cd "$FRONTEND"
npm run dev -- --host 127.0.0.1 --port $FRONTEND_PORT > "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!

wait_for "http://127.0.0.1:$FRONTEND_PORT" "Frontend"
echo "  Frontend ready (pid $FRONTEND_PID)"

# ── summary ───────────────────────────────────────────────────────────────────
echo ""
echo "SingoLing is running:"
echo "  Backend   →  http://127.0.0.1:$BACKEND_PORT"
echo "  Frontend  →  http://127.0.0.1:$FRONTEND_PORT"
echo ""
echo "Stop with:  kill $BACKEND_PID $FRONTEND_PID"
echo "Logs:       tail -f $BACKEND_LOG $FRONTEND_LOG"
