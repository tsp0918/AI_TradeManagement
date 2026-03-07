#!/usr/bin/env bash
# DAP 単体起動スクリプト（通常は start.sh から自動起動されます）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV="$ROOT/.venv/bin"

# .env を環境変数に展開（ANTHROPIC_API_KEY 等）
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT/.env"
  set +a
fi

PYTHONPATH="$SCRIPT_DIR:$ROOT/platform-core" \
  exec "$VENV/uvicorn" app.main:app \
    --host 0.0.0.0 \
    --port 8010 \
    --reload \
    --app-dir "$SCRIPT_DIR"
