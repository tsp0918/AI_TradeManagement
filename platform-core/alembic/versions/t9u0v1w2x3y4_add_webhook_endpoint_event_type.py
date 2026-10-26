"""add_webhook_endpoint_event_type

Revision ID: t9u0v1w2x3y4
Revises: s8t9u0v1w2x3
Create Date: 2026-10-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "t9u0v1w2x3y4"
down_revision = "s8t9u0v1w2x3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhook_endpoint",
        sa.Column("event_type", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_webhook_endpoint_event_type",
        "webhook_endpoint",
        ["event_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_endpoint_event_type", table_name="webhook_endpoint")
    op.drop_column("webhook_endpoint", "event_type")
