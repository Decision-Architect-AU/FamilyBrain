#!/usr/bin/env bash
# Start only the core profile (postgres, ollama, dashboard, audit-logger)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="docker compose -f $SCRIPT_DIR/../docker-compose.yml --env-file $SCRIPT_DIR/../.env"

echo "[familybrain] Starting core services..."
$COMPOSE --profile core up -d

echo "[familybrain] Writing mode file to shared volume..."
docker exec familybrain-dashboard sh -c "echo core > /shared/current_mode" 2>/dev/null \
    && echo "[familybrain] Mode set to: core" \
    || echo "[familybrain] Warning: could not write mode file"

echo "[familybrain] Core services up. Dashboard: http://localhost:3000"
