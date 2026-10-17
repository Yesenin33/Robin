"""widen tokens.pair_address for Uniswap-V4 pool ids

Robinhood Chain pools are V4-style, so the "pair" is a 32-byte pool id
(0x + 64 hex chars = 66 chars), which does not fit the original varchar(42).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('tokens', 'pair_address', existing_type=sa.String(length=42),
                    type_=sa.String(length=78), existing_nullable=True)


def downgrade() -> None:
    op.alter_column('tokens', 'pair_address', existing_type=sa.String(length=78),
                    type_=sa.String(length=42), existing_nullable=True)
