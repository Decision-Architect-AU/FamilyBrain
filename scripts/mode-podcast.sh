#!/usr/bin/env bash
# Switch to podcast mode: stop normal agents/scraper, start podcast services
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="docker compose -f $SCRIPT_DIR/../docker-compose.yml --env-file $SCRIPT_DIR/../.env"
MODE_FILE="/tmp/openclaw_current_mode"

echo "[mode-switch] Stopping normal profile agents and scraper..."
$COMPOSE --profile normal stop agents scraper n8n 2>/dev/null || true

echo "[mode-switch] Starting podcast profile..."
$COMPOSE --profile podcast up -d

echo "podcast" > "$MODE_FILE"
echo "[mode-switch] Mode: PODCAST"
