"""add plat_ux_events table for behavioral VoC collection

Revision ID: n2o3p4q5r6s7
Revises: m1n2o3p4q5r6
Create Date: 2026-06-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "n2o3p4q5r6s7"
down_revision = "m1n2o3p4q5r6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plat_ux_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(120), nullable=True),
        sa.Column("module_key", sa.String(50), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("event_name", sa.String(100), nullable=False),
        sa.Column("context", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plat_ux_events_session_id",   "plat_ux_events", ["session_id"])
    op.create_index("ix_plat_ux_events_module_key",   "plat_ux_events", ["module_key"])
    op.create_index("ix_plat_ux_events_event_type",   "plat_ux_events", ["event_type"])
    op.create_index("ix_plat_ux_events_event_name",   "plat_ux_events", ["event_name"])
    op.create_index("ix_plat_ux_events_created_at",   "plat_ux_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_plat_ux_events_created_at",  table_name="plat_ux_events")
    op.drop_index("ix_plat_ux_events_event_name",  table_name="plat_ux_events")
    op.drop_index("ix_plat_ux_events_event_type",  table_name="plat_ux_events")
    op.drop_index("ix_plat_ux_events_module_key",  table_name="plat_ux_events")
    op.drop_index("ix_plat_ux_events_session_id",  table_name="plat_ux_events")
    op.drop_table("plat_ux_events")
