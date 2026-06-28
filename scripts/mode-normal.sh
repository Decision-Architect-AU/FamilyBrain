#!/usr/bin/env bash
# Switch to normal mode: stop podcast services, start normal services
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="docker compose -f $SCRIPT_DIR/../docker-compose.yml --env-file $SCRIPT_DIR/../.env"
MODE_FILE="/tmp/familybrain_current_mode"

echo "[mode-switch] Stopping podcast profile..."
$COMPOSE --profile podcast stop podcast-agents tts whisper 2>/dev/null || true

echo "[mode-switch] Starting normal profile..."
$COMPOSE --profile normal up -d

echo "normal" > "$MODE_FILE"
echo "[mode-switch] Mode: NORMAL"
