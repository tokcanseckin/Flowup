#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../.venv/bin/activate"
exec python -u "$SCRIPT_DIR/import_playlist.py" \
  --csv "$SCRIPT_DIR/../content_to_add/songs/beginner_russian_playlist.csv" \
  --lang ru \
  --playlist-id 4 \
  --admin-token '1.2acd3c7384df01607bd3deb237f1995316edfcab01ad1483d3d8e7af21929275' \
  --api-url 'https://singoling.com' \
  "$@"
