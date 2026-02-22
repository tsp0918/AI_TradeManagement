# rd-risk-app (PoC)

R&D起案の「スナップショット評価 + 再評価（profile version管理）」に特化したリスク評価アプリ。

## Tech Stack
- FastAPI
- SQLAlchemy 2.x
- Alembic
- SQLite (PoC)

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

alembic revision --autogenerate -m "init"
alembic upgrade head

uvicorn app.main:app --reload
