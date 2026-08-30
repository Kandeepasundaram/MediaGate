#!/bin/sh
# Seeds /config/config.yaml from the container's default on first boot (an
# empty /config volume), then hands off to uvicorn. On every later boot the
# existing config.yaml (with whatever was set via the Settings UI) is left
# alone.
set -e

CONFIG_PATH="${MEDIA_MANAGER_CONFIG:-/config/config.yaml}"
mkdir -p "$(dirname "$CONFIG_PATH")"

if [ ! -f "$CONFIG_PATH" ]; then
  echo "No config found at $CONFIG_PATH, seeding container defaults"
  cp /app/config.docker.yaml "$CONFIG_PATH"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
