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
  for PORT in 5001 8011 8002 8003 8004 8005 8006 8010 8012 8013 8014 11434; do
    PID_PORT=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
    if [[ -n "$PID_PORT" ]]; then
      kill "$PID_PORT" 2>/dev/null && info "ポート $PORT (PID $PID_PORT) を解放しました"
    fi
  done
  # Cloudflare Tunnel 停止
  _CF_PID=$(pgrep -f "cloudflared tunnel run" 2>/dev/null || true)
  if [[ -n "$_CF_PID" ]]; then
    kill "$_CF_PID" 2>/dev/null && ok "Cloudflare Tunnel (PID $_CF_PID) を停止しました"
  fi
  exit 0
fi

# ── トンネルステータス確認コマンド ─────────────────────────────────
if [[ "${1:-}" == "--tunnel-status" ]]; then
  echo ""
  echo -e "${BOLD}═══════════════════════════════════════════════${RESET}"
  echo -e "${BOLD}  Cloudflare Tunnel ステータス${RESET}"
  echo -e "${BOLD}═══════════════════════════════════════════════${RESET}"
  _CF_PID=$(pgrep -f "cloudflared tunnel run" 2>/dev/null || true)
  if [[ -n "$_CF_PID" ]]; then
    ok "Tunnel プロセス実行中 (PID $_CF_PID)"
  else
    error "Tunnel プロセスが停止しています"
    warn "  再起動: ./start.sh --restart-tunnel"
  fi
  cloudflared tunnel info c021837a-9593-4c04-8be8-3477297d5649 2>&1 | head -10
  echo ""
  echo -e "  アクセス URL:"
  echo -e "  ${CYAN}https://app.tsp-aitrademanagement.com${RESET}           (portal)"
  echo -e "  ${CYAN}https://dap.tsp-aitrademanagement.com${RESET}           (DAP)"
  echo -e "  ${CYAN}https://validation.tsp-aitrademanagement.com${RESET}    (ai_validation)"
  echo -e "  ${CYAN}https://classification.tsp-aitrademanagement.com${RESET} (ai_classification)"
  exit 0
fi

# ── トンネル単体再起動 ──────────────────────────────────────────────
if [[ "${1:-}" == "--restart-tunnel" ]]; then
  _CF_PID=$(pgrep -f "cloudflared tunnel run" 2>/dev/null || true)
  if [[ -n "$_CF_PID" ]]; then
    kill "$_CF_PID" 2>/dev/null && info "既存の Tunnel プロセスを停止しました"
    sleep 1
  fi
  info "Cloudflare Tunnel を再起動します…"
  cloudflared --config ~/.cloudflared/config.yml tunnel run c021837a-9593-4c04-8be8-3477297d5649 &
  ok "Tunnel 起動済み (PID $!)"
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

# ── Cloudflare Tunnel ───────────────────────────────────────────────
if command -v cloudflared &>/dev/null; then
  _CF_PID=$(pgrep -f "cloudflared tunnel run" 2>/dev/null || true)
  if [[ -n "$_CF_PID" ]]; then
    ok "Cloudflare Tunnel: 既に実行中 (PID $_CF_PID)"
  else
    info "Cloudflare Tunnel を起動します…"
    cloudflared --config ~/.cloudflared/config.yml tunnel run c021837a-9593-4c04-8be8-3477297d5649 &>/tmp/cloudflared.log &
    _CF_NEW_PID=$!
    sleep 2
    if kill -0 "$_CF_NEW_PID" 2>/dev/null; then
      ok "Cloudflare Tunnel: 起動済み (PID $_CF_NEW_PID)"
      info "  外部 URL: https://app.tsp-aitrademanagement.com"
    else
      warn "Cloudflare Tunnel の起動に失敗しました。ログ: /tmp/cloudflared.log"
    fi
  fi
else
  warn "cloudflared が見つかりません。外部アクセスは無効です。"
  warn "  インストール: brew install cloudflared"
fi
echo ""

# ── ai_validation (port 8011) をバックグラウンドで起動 ──────────────
# ポート 8001 は Docker (faq_fastapi) が使用中のため 8011 を使用する
_VAL_DIR="$SCRIPT_DIR/modules/ai_validation"
_VAL_OLD=$(lsof -ti tcp:8011 2>/dev/null || true)
if [[ -n "$_VAL_OLD" ]]; then
  warn "ポート 8011 の既存プロセス (PID $_VAL_OLD) を停止します…"
  kill "$_VAL_OLD" 2>/dev/null || true
  sleep 1
fi
info "ai_validation をポート 8011 で起動します…"
(cd "$_VAL_DIR" && MODULE_AI_VALIDATION_URL=http://localhost:8011 \
  PYTHONPATH="$_VAL_DIR:$PLATFORM_CORE" \
  exec "$VENV/uvicorn" app.main:app \
    --host 0.0.0.0 --port 8011) &
ok "ai_validation 起動済み (PID $!)"

# ── ERP Integration Adapter (port 5001) をバックグラウンドで起動 ─────
_ERP_INT_OLD=$(lsof -ti tcp:5001 2>/dev/null || true)
if [[ -n "$_ERP_INT_OLD" ]]; then
  warn "ポート 5001 の既存プロセス (PID $_ERP_INT_OLD) を停止します…"
  kill "$_ERP_INT_OLD" 2>/dev/null || true
  sleep 1
fi
info "ERP Integration Adapter をポート 5001 で起動します…"
(cd "$SCRIPT_DIR" && PYTHONPATH="$PLATFORM_CORE" \
  exec "$VENV/uvicorn" modules.erp_integration.app.main:app \
    --host 0.0.0.0 --port 5001) &
ok "ERP Integration Adapter 起動済み (PID $!)"

# ── 各機能モジュール (8002–8006) をバックグラウンドで起動 ──────────────
for _MOD_ENTRY in "ai_classification:8002" "rnd_assessment:8003" "patent_search:8004" "screening:8005" "hs_classifier:8006" "export_license:8012"; do
  _MOD_KEY="${_MOD_ENTRY%%:*}"
  _MOD_PORT="${_MOD_ENTRY##*:}"
  _MOD_DIR="$SCRIPT_DIR/modules/$_MOD_KEY"
  _MOD_OLD=$(lsof -ti tcp:"$_MOD_PORT" 2>/dev/null || true)
  if [[ -n "$_MOD_OLD" ]]; then
    warn "ポート $_MOD_PORT の既存プロセス (PID $_MOD_OLD) を停止します…"
    kill "$_MOD_OLD" 2>/dev/null || true
    sleep 0.5
  fi
  info "$_MOD_KEY をポート $_MOD_PORT で起動します…"
  (cd "$_MOD_DIR" && PYTHONPATH="$_MOD_DIR:$PLATFORM_CORE" \
    exec "$VENV/uvicorn" app.main:app \
      --host 0.0.0.0 --port "$_MOD_PORT" \
      >/tmp/${_MOD_KEY}.log 2>&1) &
  ok "$_MOD_KEY 起動済み (PID $!)"
done

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
  info "ローカルアクセス  → http://localhost:${PLATFORM_PORT}"
  info "外部アクセス     → https://app.tsp-aitrademanagement.com"
  info "DAP (外部)       → https://dap.tsp-aitrademanagement.com"
  info "トンネル確認     → ./start.sh --tunnel-status"
  echo ""
  PYTHONPATH="$PLATFORM_CORE" \
    exec "$VENV/uvicorn" platform_core.main:app \
      --host 0.0.0.0 \
      --port "$PLATFORM_PORT"
fi
