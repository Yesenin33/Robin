"""safety_scores.score may be NULL (verdict UNKNOWN: not enough evidence to score)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('safety_scores', 'score', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE safety_scores SET score = 0 WHERE score IS NULL")
    op.alter_column('safety_scores', 'score', existing_type=sa.Integer(), nullable=False)
