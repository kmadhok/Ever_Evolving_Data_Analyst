#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DST="/etc/systemd/system/ever-evolving-scheduler.service"
USER_NAME="$(id -un)"
GOOGLE_CREDENTIALS_PATH="${GOOGLE_APPLICATION_CREDENTIALS:-$ROOT_DIR/credentials_brainrot.json}"
KALSHI_KEY_PATH="${KALSHI_PRIVATE_KEY_PATH:-$ROOT_DIR/kalshi_private_key.pem}"
TMP_UNIT="$(mktemp)"

trap 'rm -f "$TMP_UNIT"' EXIT

"$ROOT_DIR/scripts/scheduler_smoke_check.sh"

cat > "$TMP_UNIT" <<EOF
[Unit]
Description=Ever Evolving Software Scheduler
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$ROOT_DIR
Environment=GOOGLE_APPLICATION_CREDENTIALS=$GOOGLE_CREDENTIALS_PATH
Environment=KALSHI_PRIVATE_KEY_PATH=$KALSHI_KEY_PATH
ExecStart=$ROOT_DIR/scripts/run_scheduler.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo install -m 0644 "$TMP_UNIT" "$UNIT_DST"
sudo systemctl daemon-reload
sudo systemctl enable --now ever-evolving-scheduler.service
sudo systemctl status --no-pager ever-evolving-scheduler.service
