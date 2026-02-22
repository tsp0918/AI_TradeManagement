# scripts/create_db.py
import sys
from pathlib import Path

# プロジェクトルート（AI_validation/）を import パスに入れる
ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from app.db.session import engine
from app.db.models import Base  # noqa: F401


def main():
    Base.metadata.create_all(bind=engine)
    print("DB tables created.")


if __name__ == "__main__":
    main()
