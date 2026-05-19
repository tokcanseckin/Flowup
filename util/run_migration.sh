#!/usr/bin/env bash
# Run database migration on production server
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER="root@185.20.139.134"
BACKEND_REMOTE="/opt/flowup/backend"
MIGRATION_FILE="${1:-migration_add_subscription_columns.sql}"

if [[ ! -f "$ROOT/backend/$MIGRATION_FILE" ]]; then
  echo "Error: Migration file '$MIGRATION_FILE' not found in backend/"
  exit 1
fi

echo "→ Copying migration files to server..."
rsync -az "$ROOT/backend/run_migration.py" "$SERVER:$BACKEND_REMOTE/"
rsync -az "$ROOT/backend/$MIGRATION_FILE" "$SERVER:$BACKEND_REMOTE/"

echo "→ Running migration on production database..."
ssh "$SERVER" "cd $BACKEND_REMOTE && source /opt/flowup/.venv/bin/activate && set -a && source .env && set +a && python run_migration.py $MIGRATION_FILE"

echo "✓ Migration complete!"
