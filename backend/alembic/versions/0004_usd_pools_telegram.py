"""USD valuation (pools map, quote legs, smart_wallets in USD) + Telegram alert toggle

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

BigId = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    op.create_table(
        'pools',
        sa.Column('id', BigId, autoincrement=True, nullable=False),
        sa.Column('pool_id', sa.String(78), nullable=False),
        sa.Column('currency0', sa.String(78), nullable=False),
        sa.Column('currency1', sa.String(78), nullable=False),
        sa.Column('block', BigId, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_pools_pool_id', 'pools', ['pool_id'], unique=True)

    op.add_column('copy_events', sa.Column('quote_token', sa.String(78), nullable=True))
    op.add_column('copy_events', sa.Column('quote_amount', sa.Numeric(48, 18), nullable=True))

    # ETH-denominated ranking scored 0 wallets (pools quote in USDG): re-rank in USD
    op.execute("DELETE FROM smart_wallets")
    op.alter_column('smart_wallets', 'realised_pnl_eth', new_column_name='realised_pnl_usd')
    op.alter_column('smart_wallets', 'invested_eth', new_column_name='invested_usd')

    op.add_column('users', sa.Column('telegram_alerts', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_index('ix_users_telegram_chat_id', 'users', ['telegram_chat_id'])


def downgrade() -> None:
    op.drop_index('ix_users_telegram_chat_id', 'users')
    op.drop_column('users', 'telegram_alerts')
    op.execute("DELETE FROM smart_wallets")
    op.alter_column('smart_wallets', 'invested_usd', new_column_name='invested_eth')
    op.alter_column('smart_wallets', 'realised_pnl_usd', new_column_name='realised_pnl_eth')
    op.drop_column('copy_events', 'quote_amount')
    op.drop_column('copy_events', 'quote_token')
    op.drop_index('ix_pools_pool_id', 'pools')
    op.drop_table('pools')
