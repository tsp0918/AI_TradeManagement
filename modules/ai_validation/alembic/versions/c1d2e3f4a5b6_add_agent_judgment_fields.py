"""add agent judgment fields to transactions

Revision ID: c1d2e3f4a5b6
Revises: f3a7e2d1c0b9
Create Date: 2026-03-26 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = ("b7e8f9a0b1c2", "f3a7e2d1c0b9")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # エージェント判定結果 (controlled / not_controlled / requires_review)
    op.add_column("transactions", sa.Column("agent_judgment_status", sa.String(32), nullable=True))
    # 判定完了日時
    op.add_column("transactions", sa.Column("agent_judged_at", sa.DateTime(), nullable=True))
    # 正式審査提出日時
    op.add_column("transactions", sa.Column("formal_submitted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "formal_submitted_at")
    op.drop_column("transactions", "agent_judged_at")
    op.drop_column("transactions", "agent_judgment_status")
