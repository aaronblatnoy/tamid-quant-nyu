#!/usr/bin/env bash
# Bootstrap a fresh local dev environment for taquantgeo.
# Idempotent — safe to re-run.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Checking uv"
if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv to ~/.local/bin"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> Syncing workspace"
uv sync

echo "==> Installing pre-commit hooks"
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg

if [[ ! -f .env ]]; then
    echo "==> Creating .env from template"
    cp .env.example .env
    echo "  ⚠  Fill in .env before running anything that hits external services."
fi

echo "==> Starting local postgres + redis (docker-compose)"
if command -v docker >/dev/null 2>&1; then
    docker compose -f infra/docker-compose.yml up -d
else
    echo "  ⚠  Docker not available. Install Docker Desktop and enable WSL2 integration."
fi

echo "==> Smoke test"
uv run pytest tests/unit/test_smoke.py -v

echo "==> Done. Try: uv run taq --help"
