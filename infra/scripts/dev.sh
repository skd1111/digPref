#!/usr/bin/env bash
# dev.sh — one-shot dev launcher (Unix/macOS).
# For Windows use `make dev` or `.\infra\scripts\dev.ps1`.

set -euo pipefail
cd "$(dirname "$0")/../.."

echo "▶ Starting infra (Agent + MCP servers)..."
docker compose -f infra/docker/docker-compose.dev.yml --profile full up -d --build

echo "▶ Starting Python Agent in dev mode (foreground)..."
(cd services/agent && uv run uvicorn agent.main:app --reload --port 8765) &

echo "▶ Starting Tauri desktop in dev mode..."
(cd apps/desktop && pnpm tauri dev)

trap "docker compose -f infra/docker/docker-compose.dev.yml down" EXIT
wait