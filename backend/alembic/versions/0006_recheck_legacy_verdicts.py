"""One-off data fix for the "unknown == safe" class

Before the evidence bar existed a scan with missing data was stored as CLEAN/RISKY with a green "—" metric. Those
rows are read directly by the portfolio badges and the copy feed.

1. A CLEAN/RISKY row that shows a value-less metric becomes UNKNOWN (no score).
2. Every row from the last day is marked incomplete, so the next view re-scans it under the current rules
   instead of serving the old verdict from the "recent DB row" shortcut.

Data only; nothing to undo. PostgreSQL only (the JSON functions); other dialects start empty.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-18
"""
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
        UPDATE safety_scores SET verdict = 'UNKNOWN', score = NULL
        WHERE verdict IN ('CLEAN', 'RISKY') AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(COALESCE(metrics::jsonb -> 'items', '[]'::jsonb)) AS m
            WHERE m ->> 'value' = '—')
    """)
    op.execute("""
        UPDATE safety_scores
        SET metrics = jsonb_set(COALESCE(metrics::jsonb, '{}'::jsonb), '{incomplete}', 'true'::jsonb)::json
        WHERE ts > now() - interval '1 day'
    """)


def downgrade() -> None:
    pass  # data-only fix
