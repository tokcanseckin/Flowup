#!/usr/bin/env bash
# SingoLing deployment script
# Server: root@185.20.139.134
# Backend service: flowup-backend.service (systemd)
# Frontend: nginx at /var/www/flowup, domain singoling.com
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER="root@185.20.139.134"
BACKEND_REMOTE="/opt/flowup/backend"
FRONTEND_REMOTE="/var/www/flowup"

# ── 1. Commit & push ──────────────────────────────────────────────────────────
if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
  echo "Uncommitted changes found. Please commit before deploying."
  git -C "$ROOT" status --short
  exit 1
fi
echo "→ Pushing to GitHub…"
git -C "$ROOT" push

# ── 2. Deploy backend ─────────────────────────────────────────────────────────
echo "→ Syncing backend…"
rsync -az \
  --exclude='.env' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='flowup.db' \
  --exclude='.venv' \
  "$ROOT/backend/" "$SERVER:$BACKEND_REMOTE/"

# ── 3. Build & deploy frontend ────────────────────────────────────────────────
echo "→ Building frontend…"
cd "$ROOT/frontend"
npm run build

echo "→ Syncing frontend…"
rsync -az "$ROOT/frontend/dist/" "$SERVER:$FRONTEND_REMOTE/"

# ── 4. Restart backend service ────────────────────────────────────────────────
echo "→ Restarting backend service…"
ssh "$SERVER" "systemctl restart flowup-backend && systemctl is-active flowup-backend"

echo ""
echo "✓ Deployed to singoling.com"
