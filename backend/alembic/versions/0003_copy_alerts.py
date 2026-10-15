"""copy feed + alerts: smart_wallets, push_subscriptions, richer copy_events, tokens.pool_init_block

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

BigId = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    op.add_column('tokens', sa.Column('pool_init_block', BigId, nullable=True))

    op.add_column('copy_events', sa.Column('token_amount', sa.Numeric(48, 18), nullable=True))
    op.add_column('copy_events', sa.Column('eth_amount', sa.Numeric(38, 18), nullable=True))
    op.add_column('copy_events', sa.Column('block_number', BigId, nullable=True))
    op.alter_column('copy_events', 'tx_hash', existing_type=sa.String(66), type_=sa.String(78))
    op.drop_constraint('copy_events_tx_hash_key', 'copy_events', type_='unique')
    op.create_unique_constraint('uq_copy_events_tx_token_wallet_side', 'copy_events',
                                ['tx_hash', 'token_address', 'address', 'side'])

    op.create_table(
        'smart_wallets',
        sa.Column('id', BigId, autoincrement=True, nullable=False),
        sa.Column('address', sa.String(78), nullable=False),
        sa.Column('score', sa.Numeric(24, 8), nullable=False),
        sa.Column('realised_pnl_eth', sa.Numeric(38, 18), nullable=False),
        sa.Column('invested_eth', sa.Numeric(38, 18), nullable=False),
        sa.Column('trades', sa.Integer(), nullable=False),
        sa.Column('wins', sa.Integer(), nullable=False),
        sa.Column('losses', sa.Integer(), nullable=False),
        sa.Column('tokens', sa.Integer(), nullable=False),
        sa.Column('early_buys', sa.Integer(), nullable=False),
        sa.Column('last_block', BigId, nullable=True),
        sa.Column('token_list', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_smart_wallets_address', 'smart_wallets', ['address'], unique=True)
    op.create_index('ix_smart_wallets_score', 'smart_wallets', ['score'])

    op.create_table(
        'push_subscriptions',
        sa.Column('id', BigId, autoincrement=True, nullable=False),
        sa.Column('user_id', BigId, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('endpoint', sa.String(1024), nullable=False),
        sa.Column('p256dh', sa.String(256), nullable=False),
        sa.Column('auth', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('endpoint'),
    )
    op.create_index('ix_push_subscriptions_user_id', 'push_subscriptions', ['user_id'])


def downgrade() -> None:
    op.drop_table('push_subscriptions')
    op.drop_table('smart_wallets')
    op.drop_constraint('uq_copy_events_tx_token_wallet_side', 'copy_events', type_='unique')
    op.alter_column('copy_events', 'tx_hash', existing_type=sa.String(78), type_=sa.String(66))
    op.create_unique_constraint('copy_events_tx_hash_key', 'copy_events', ['tx_hash'])
    for c in ('block_number', 'eth_amount', 'token_amount'):
        op.drop_column('copy_events', c)
    op.drop_column('tokens', 'pool_init_block')
