#!/bin/bash

# 特許検索アプリ起動スクリプト
# 初回起動時に仮想環境を作成し、依存関係をインストールしてからuvicornで起動します

set -e  # エラーが発生したら停止

# 色付き出力用
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== 特許検索アプリ起動 ===${NC}\n"

# 仮想環境のディレクトリ
VENV_DIR="venv"

# 仮想環境が存在しない場合は作成
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}仮想環境が見つかりません。新規作成します...${NC}"
    python3 -m venv $VENV_DIR
    echo -e "${GREEN}✓ 仮想環境を作成しました${NC}\n"
else
    echo -e "${GREEN}✓ 仮想環境が見つかりました${NC}\n"
fi

# 仮想環境をアクティベート
echo -e "${BLUE}仮想環境をアクティベート中...${NC}"
source $VENV_DIR/bin/activate

# pipをアップグレード
echo -e "${BLUE}pipをアップグレード中...${NC}"
pip install --upgrade pip --quiet

# 依存関係をインストール（初回または更新時）
if [ ! -f "$VENV_DIR/.installed" ] || [ requirements.txt -nt "$VENV_DIR/.installed" ]; then
    echo -e "${YELLOW}依存関係をインストール中...${NC}"
    pip install -r requirements.txt
    touch $VENV_DIR/.installed
    echo -e "${GREEN}✓ 依存関係をインストールしました${NC}\n"
else
    echo -e "${GREEN}✓ 依存関係は既にインストール済みです${NC}\n"
fi

# .envファイルの確認
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}⚠ .envファイルが見つかりません${NC}"
    echo -e "${YELLOW}  .env.exampleを参考に.envファイルを作成してください${NC}\n"
fi

# uvicornでアプリケーションを起動
echo -e "${GREEN}=== アプリケーション起動 ===${NC}\n"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
