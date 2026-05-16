#!/usr/bin/env bash
set -e
source .venv/bin/activate
# Load backend env vars (DATABASE_URL etc.)
set -a; source backend/.env; set +a
echo "[$(date +%H:%M:%S)] Starting fill_word_translations en_ru …"
python3 -m pipeline.fill_word_translations --pair en_ru --min-id 248
echo "[$(date +%H:%M:%S)] Starting fill_line_translations en → ru …"
python3 -m pipeline.fill_line_translations --src en --tgt ru --min-id 217
echo "[$(date +%H:%M:%S)] All fills complete."
