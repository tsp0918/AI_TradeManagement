# DAP Coach (Local)

## 前提
- 対象アプリ（R&Dプロジェクト審査アプリ）： http://localhost:8000
- DAPサーバ（Admin + Runtime）： http://localhost:8710
- Chrome拡張：dap-extension を Load unpacked で読み込み

## 起動手順
### 1) DAPサーバ起動
```bash
cd dap-server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./run.sh
