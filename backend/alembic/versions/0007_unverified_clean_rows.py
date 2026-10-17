"""Demote stored CLEAN/RISKY rows whose holders, real-hands or liquidity metric is missing

The Safety screen, portfolio badges and copy feed read the latest stored row. Rows written before the evidence bar
can claim CLEAN next to empty metrics; readers now also recompute (verdict_guard), this fixes the stored data.
Data only; PostgreSQL only.

Revision ID: 0007
Revises: 0006
"""
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
        UPDATE safety_scores SET verdict = 'UNKNOWN', score = NULL
        WHERE verdict IN ('CLEAN', 'RISKY') AND NOT (
            SELECT count(*) = 3 FROM jsonb_array_elements(COALESCE(metrics::jsonb -> 'items', '[]'::jsonb)) AS m
            WHERE m ->> 'key' IN ('holders', 'real_hands', 'liquidity') AND COALESCE(m ->> 'value', '—') NOT IN ('—', ''))
    """)


def downgrade() -> None:
    pass
