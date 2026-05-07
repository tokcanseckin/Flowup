#!/usr/bin/env bash
# FlowUp Alignment Worker — start script
#
# Activates the local venv and runs the worker polling loop.
#
# Usage:
#   bash start.sh           # run continuously
#   bash start.sh --once    # process one pending task and exit

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Error: venv not found at $SCRIPT_DIR/.venv"
    echo "Run install.sh first."
    exit 1
fi

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    echo "Error: .env not found."
    echo "Copy .env.example to .env and fill in REMOTE_API_URL and WORKER_API_KEY."
    exit 1
fi

exec "$VENV_PYTHON" "$SCRIPT_DIR/worker.py" "$@"
