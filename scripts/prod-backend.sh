#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../backend"
alembic upgrade head
exec uvicorn app.main:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-5000}"
