"""add_webhook_endpoint_bearer

Revision ID: u0v1w2x3y4z5
Revises: t9u0v1w2x3y4
Create Date: 2026-10-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "u0v1w2x3y4z5"
down_revision = "t9u0v1w2x3y4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhook_endpoint",
        sa.Column("bearer", sa.String(256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("webhook_endpoint", "bearer")
