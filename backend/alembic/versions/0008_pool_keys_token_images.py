"""Persist v4 PoolKey parts (fee, tick spacing, hooks) and token images

The swap no longer needs a historical log read: discovery stores what the Initialize event already carries.
Token images (DexScreener info.imageUrl) are captured at scan time and exposed next to the symbol.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pools', sa.Column('fee', sa.Integer(), nullable=True))
    op.add_column('pools', sa.Column('tick_spacing', sa.Integer(), nullable=True))
    op.add_column('pools', sa.Column('hooks', sa.String(42), nullable=True))
    op.add_column('tokens', sa.Column('image_url', sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column('tokens', 'image_url')
    op.drop_column('pools', 'hooks')
    op.drop_column('pools', 'tick_spacing')
    op.drop_column('pools', 'fee')
