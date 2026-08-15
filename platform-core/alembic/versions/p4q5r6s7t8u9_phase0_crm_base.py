"""Phase 0 CRM integration base: tenant mapping + webhook tables

Revision ID: p4q5r6s7t8u9
Revises: o3p4q5r6s7t8
Create Date: 2026-08-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "p4q5r6s7t8u9"
down_revision = "o3p4q5r6s7t8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── plat_tenant に CRM/ERP マッピング列を追加 ─────────────────────────
    op.add_column("plat_tenant", sa.Column("crm_tenant_id", sa.String(64), nullable=True))
    op.add_column("plat_tenant", sa.Column("erp_tenant_code", sa.String(64), nullable=True))
    op.add_column("plat_tenant", sa.Column("crm_signing_secret", sa.String(256), nullable=True))
    op.add_column("plat_tenant", sa.Column("erp_signing_secret", sa.String(256), nullable=True))
    op.create_index("ix_plat_tenant_crm_tenant_id", "plat_tenant", ["crm_tenant_id"])

    # ── webhook_endpoint テーブル ──────────────────────────────────────────
    op.create_table(
        "webhook_endpoint",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plat_tenant.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("target_system", sa.String(20), nullable=False),  # 'crm' | 'erp'
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("signing_secret", sa.String(256), nullable=False),
        sa.Column("is_active", sa.Boolean, server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── webhook_delivery テーブル ──────────────────────────────────────────
    op.create_table(
        "webhook_delivery",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "endpoint_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("webhook_endpoint.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(20), server_default="'pending'", nullable=False),
        sa.Column("attempt_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_webhook_delivery_status_retry",
        "webhook_delivery",
        ["status", "next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_delivery_status_retry", table_name="webhook_delivery")
    op.drop_table("webhook_delivery")
    op.drop_table("webhook_endpoint")
    op.drop_index("ix_plat_tenant_crm_tenant_id", table_name="plat_tenant")
    op.drop_column("plat_tenant", "erp_signing_secret")
    op.drop_column("plat_tenant", "crm_signing_secret")
    op.drop_column("plat_tenant", "erp_tenant_code")
    op.drop_column("plat_tenant", "crm_tenant_id")
