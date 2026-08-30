#!/usr/bin/env bash
# Backs up the SQLite database and config.yaml. Intended for a daily cron entry.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

DB_PATH="$(cd "$APP_DIR" && .venv/bin/python -c "from app.config_loader import load_config; print(load_config().database_path)")"

if [[ -f "$DB_PATH" ]]; then
  sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/media_manager_$TIMESTAMP.db'"
  echo "Database backed up to $BACKUP_DIR/media_manager_$TIMESTAMP.db"
else
  echo "No database found at $DB_PATH, skipping db backup" >&2
fi

cp "$APP_DIR/config.yaml" "$BACKUP_DIR/config_$TIMESTAMP.yaml"
echo "Config backed up to $BACKUP_DIR/config_$TIMESTAMP.yaml"

# Keep the last 30 backups of each type
find "$BACKUP_DIR" -name 'media_manager_*.db' -type f | sort | head -n -30 | xargs -r rm
find "$BACKUP_DIR" -name 'config_*.yaml' -type f | sort | head -n -30 | xargs -r rm
