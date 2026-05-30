"""Add inventory system: SKU, stock tracking, movement log, notification log

Revision ID: b7c2e9d40f15
Revises: 73b8b753c510
Create Date: 2026-05-28 12:05:00.000000

Changes:
  1. menu_items: ADD COLUMN sku VARCHAR(64)
  2. menu_items: ADD COLUMN stock_quantity FLOAT
  3. menu_items: ADD COLUMN low_stock_threshold FLOAT DEFAULT 5
  4. CREATE TABLE stock_movements (audit log for every stock change)
  5. CREATE TABLE notification_log (provision for future channels)
  6. CREATE UNIQUE INDEX uq_sku_per_shop ON menu_items(shop_id, sku)
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b7c2e9d40f15'
down_revision = '73b8b753c510'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Add SKU and inventory columns to menu_items
    # -------------------------------------------------------------------------
    op.add_column('menu_items', sa.Column('sku', sa.String(length=64), nullable=True))
    op.add_column('menu_items', sa.Column('stock_quantity', sa.Float(), nullable=True))
    op.add_column('menu_items', sa.Column('low_stock_threshold', sa.Float(), nullable=True, server_default='5'))

    # Composite index on (shop_id, sku) — used by scanner lookup
    op.create_index('ix_menuitems_shop_sku', 'menu_items', ['shop_id', 'sku'], unique=False)

    # -------------------------------------------------------------------------
    # 2. Create stock_movements table
    # -------------------------------------------------------------------------
    op.create_table(
        'stock_movements',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('menu_item_id', sa.Integer(), sa.ForeignKey('menu_items.id', ondelete='CASCADE'), nullable=False),
        sa.Column('shop_id', sa.Integer(), sa.ForeignKey('shops.id', ondelete='CASCADE'), nullable=False),
        sa.Column('change_qty', sa.Float(), nullable=False),
        sa.Column('reason', sa.String(length=32), nullable=False, server_default='adjustment'),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_stock_movements_shop_created', 'stock_movements', ['shop_id', 'created_at'])
    op.create_index('ix_stock_movements_item_created', 'stock_movements', ['menu_item_id', 'created_at'])

    # -------------------------------------------------------------------------
    # 3. Create notification_log table
    # -------------------------------------------------------------------------
    op.create_table(
        'notification_log',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('shop_id', sa.Integer(), sa.ForeignKey('shops.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel', sa.String(length=32), nullable=False, server_default='in_app'),
        sa.Column('message', sa.String(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='sent'),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_notification_log_shop_sent', 'notification_log', ['shop_id', 'sent_at'])


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_index('ix_notification_log_shop_sent', table_name='notification_log')
    op.drop_table('notification_log')

    op.drop_index('ix_stock_movements_item_created', table_name='stock_movements')
    op.drop_index('ix_stock_movements_shop_created', table_name='stock_movements')
    op.drop_table('stock_movements')

    op.drop_index('ix_menuitems_shop_sku', table_name='menu_items')
    op.drop_column('menu_items', 'low_stock_threshold')
    op.drop_column('menu_items', 'stock_quantity')
    op.drop_column('menu_items', 'sku')
