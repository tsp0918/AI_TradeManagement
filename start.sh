#!/usr/bin/env bash
# =============================================================================
# start.sh — AI Trade Management プラットフォーム起動スクリプト
#
# 使い方:
#   ./start.sh            # 通常起動
#   ./start.sh --dev      # ホットリロード付き開発モード
#   ./start.sh --stop     # 起動中のプロセスをすべて停止
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv/bin"
PLATFORM_CORE="$SCRIPT_DIR/platform-core"
PLATFORM_PORT=8000
PIDFILE="$SCRIPT_DIR/.platform.pid"

# ── カラー出力 ──────────────────────────────────────────────────
RESET='\033[0m'; BOLD='\033[1m'
GREEN='\033[0;32m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'

info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }

# ── 停止モード ─────────────────────────────────────────────────
if [[ "${1:-}" == "--stop" ]]; then
  info "プロセスを停止しています…"
  if [[ -f "$PIDFILE" ]]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      kill "$PID" && ok "platform-core (PID $PID) を停止しました"
    fi
    rm -f "$PIDFILE"
  else
    warn "PID ファイルが見つかりません。手動で確認してください。"
  fi
  # モジュールポートを強制解放
  for PORT in 8001 8002 8003 8004 8005 8010 11434; do
    PID_PORT=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
    if [[ -n "$PID_PORT" ]]; then
      kill "$PID_PORT" 2>/dev/null && info "ポート $PORT (PID $PID_PORT) を解放しました"
    fi
  done
  exit 0
fi

# ── .env 読み込み（ANTHROPIC_API_KEY 等を環境変数に展開）──────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$SCRIPT_DIR/.env"
  set +a
  ok ".env を読み込みました"
fi

# ── 前提チェック ────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  AI Trade Management  — 起動チェック${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════════${RESET}"
echo ""

# Python venv
if [[ ! -f "$VENV/python3" ]]; then
  error "仮想環境が見つかりません: $VENV"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
ok "Python venv: $VENV"

# uvicorn
if [[ ! -f "$VENV/uvicorn" ]]; then
  error "uvicorn が見つかりません。pip install uvicorn を実行してください。"
  exit 1
fi
ok "uvicorn: 確認済み"

# Ollama
if command -v ollama &>/dev/null; then
  ok "Ollama: $(ollama --version 2>/dev/null | head -1)"
else
  warn "Ollama が未インストールです。AI機能 (patent_search, ai_classification) は制限されます。"
  warn "  インストール: https://ollama.ai"
fi

# PostgreSQL 接続確認 (psql が使える場合のみ)
if command -v psql &>/dev/null; then
  if psql "${DATABASE_URL:-postgresql://localhost/ai_trade}" -c "SELECT 1" &>/dev/null 2>&1; then
    ok "PostgreSQL: 接続確認済み"
  else
    warn "PostgreSQL に接続できません。DB_URL を .env で確認してください。"
  fi
fi

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  プラットフォーム起動${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════════${RESET}"
echo ""

# ── 起動 ────────────────────────────────────────────────────────
DEV_MODE=0
if [[ "${1:-}" == "--dev" ]]; then
  DEV_MODE=1
  info "開発モード (ホットリロード有効)"
fi

DAP_DIR="$SCRIPT_DIR/modules/dap"

# ── DAP (port 8010) をバックグラウンドで起動 ────────────────────────
# StaticFiles が相対パス "app/static" を使うため DAP_DIR に cd してから起動する
# 再起動時に旧プロセスが残っていると "address already in use" で即終了するため事前に解放する
_DAP_OLD=$(lsof -ti tcp:8010 2>/dev/null || true)
if [[ -n "$_DAP_OLD" ]]; then
  warn "ポート 8010 の既存プロセス (PID $_DAP_OLD) を停止します…"
  kill "$_DAP_OLD" 2>/dev/null || true
  sleep 1
fi
info "DAP をポート 8010 で起動します…"
(cd "$DAP_DIR" && PYTHONPATH="$DAP_DIR:$PLATFORM_CORE" \
  exec "$VENV/uvicorn" app.main:app \
    --host 0.0.0.0 --port 8010) &
ok "DAP 起動済み (PID $!)"

if [[ $DEV_MODE -eq 1 ]]; then
  PYTHONPATH="$PLATFORM_CORE" \
    exec "$VENV/uvicorn" platform_core.main:app \
      --host 0.0.0.0 \
      --port "$PLATFORM_PORT" \
      --reload \
      --reload-dir "$PLATFORM_CORE/platform_core"
else
  info "platform-core をポート ${PLATFORM_PORT} で起動します…"
  info "全モジュール (Ollama 含む) は lifespan で自動起動されます"
  echo ""
  info "ブラウザでアクセス → http://localhost:${PLATFORM_PORT}"
  info "DAP アシスタント → http://localhost:8010"
  echo ""
  PYTHONPATH="$PLATFORM_CORE" \
    exec "$VENV/uvicorn" platform_core.main:app \
      --host 0.0.0.0 \
      --port "$PLATFORM_PORT"
fi
