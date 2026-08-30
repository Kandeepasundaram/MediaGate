#!/usr/bin/env bash
# Pulls latest code, updates deps, runs migrations, restarts services.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

echo "Pulling latest code..."
git pull --ff-only

echo "Updating dependencies..."
.venv/bin/pip install -q -r requirements.txt

echo "Running database migrations..."
.venv/bin/python -c "from app.config_loader import load_config; from app.database import Database; Database(load_config().database_path).init_db()"

echo "Restarting service..."
sudo systemctl restart media-manager.service

echo "Deploy complete."
