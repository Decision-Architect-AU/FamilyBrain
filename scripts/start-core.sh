#!/usr/bin/env bash
# Start only the core profile (postgres, ollama, dashboard, audit-logger)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="docker compose -f $SCRIPT_DIR/../docker-compose.yml --env-file $SCRIPT_DIR/../.env"

echo "[openclaw] Starting core services..."
$COMPOSE --profile core up -d

echo "[openclaw] Writing mode file to shared volume..."
docker exec openclaw-dashboard sh -c "echo core > /shared/current_mode" 2>/dev/null \
    && echo "[openclaw] Mode set to: core" \
    || echo "[openclaw] Warning: could not write mode file"

echo "[openclaw] Core services up. Dashboard: http://localhost:3000"
