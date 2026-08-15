"""Phase 2: Party Registry — plat_party / plat_party_identifier / plat_party_merge_candidate

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-08-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "q5r6s7t8u9v0"
down_revision = "p4q5r6s7t8u9"
branch_labels = None
depends_on = None

TIMESTAMPTZ = sa.DateTime(timezone=True)
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    # ── plat_party ──────────────────────────────────────────────────────────
    op.create_table(
        "plat_party",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("plat_tenant.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("legal_name", sa.Text, nullable=False),
        sa.Column("country_code", sa.CHAR(2), nullable=True),
        sa.Column("party_type", sa.String(20), nullable=True),   # 'company' | 'individual' | 'gov'
        sa.Column("risk_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("sanction_status", sa.String(20), nullable=True),  # 'clear' | 'possible_match' | 'match'
        sa.Column("last_screened_at", TIMESTAMPTZ, nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_plat_party_legal_name", "plat_party", ["legal_name"])
    op.create_index("ix_plat_party_country", "plat_party", ["country_code"])

    # ── plat_party_identifier ────────────────────────────────────────────────
    op.create_table(
        "plat_party_identifier",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("party_id", UUID, sa.ForeignKey("plat_party.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("system", sa.String(20), nullable=False),      # 'crm' | 'erp' | 'aitm' | 'duns'
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("system", "external_id", name="uq_party_identifier_system_extid"),
    )

    # ── plat_party_merge_candidate ───────────────────────────────────────────
    op.create_table(
        "plat_party_merge_candidate",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("party_a_id", UUID, sa.ForeignKey("plat_party.id", ondelete="CASCADE"), nullable=False),
        sa.Column("party_b_id", UUID, sa.ForeignKey("plat_party.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score", sa.Numeric(4, 3), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),  # pending/merged/rejected
        sa.Column("reviewer_id", UUID, nullable=True),
        sa.Column("reviewed_at", TIMESTAMPTZ, nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_plat_party_merge_status", "plat_party_merge_candidate", ["status"])


def downgrade() -> None:
    op.drop_table("plat_party_merge_candidate")
    op.drop_table("plat_party_identifier")
    op.drop_table("plat_party")
