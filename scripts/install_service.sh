#!/usr/bin/env bash
# Installs the Media Manager FastAPI server as a systemd service.
# Run on the Ubuntu host with sudo: sudo ./scripts/install_service.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="${SERVICE_USER:-$(logname)}"
SERVICE_FILE=/etc/systemd/system/media-manager.service

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo $0)" >&2
  exit 1
fi

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Media Manager FastAPI server
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable media-manager.service
systemctl restart media-manager.service
echo "Installed and started media-manager.service"
echo "Check status: systemctl status media-manager.service"
echo "View logs:    journalctl -u media-manager.service -f"
