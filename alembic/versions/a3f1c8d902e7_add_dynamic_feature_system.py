"""Add dynamic feature system: billing_cycle/is_active to subscriptions, plan_features M2M, tenant_feature_overrides

Revision ID: a3f1c8d902e7
Revises: f98800d0c91f
Create Date: 2026-05-11 15:15:00.000000

Changes:
- subscriptions: add billing_cycle (VARCHAR(16)), is_active (BOOLEAN)
- plan_features: new M2M table linking plans to features (relational, replaces JSON)
- features: add category (VARCHAR(64)), is_active (BOOLEAN), created_at (TIMESTAMPTZ)
- tenant_feature_overrides: new table for per-tenant capability overrides
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a3f1c8d902e7'
down_revision: Union[str, Sequence[str], None] = 'f98800d0c91f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ----------------------------------------------------------------
    # 1. Extend `features` table with new columns (idempotent)
    # ----------------------------------------------------------------
    existing_feat_cols = [col['name'] for col in sa.inspect(conn).get_columns('features')]
    with op.batch_alter_table('features', schema=None) as batch_op:
        if 'category' not in existing_feat_cols:
            batch_op.add_column(sa.Column('category', sa.String(length=64), nullable=True))
        if 'is_active' not in existing_feat_cols:
            batch_op.add_column(sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False))
        if 'created_at' not in existing_feat_cols:
            batch_op.add_column(sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True))

    # ----------------------------------------------------------------
    # 2. Extend `subscriptions` table with new columns (idempotent)
    # ----------------------------------------------------------------
    existing_sub_cols = [col['name'] for col in sa.inspect(conn).get_columns('subscriptions')]
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        if 'billing_cycle' not in existing_sub_cols:
            batch_op.add_column(sa.Column('billing_cycle', sa.String(length=16), server_default='monthly', nullable=False))
        if 'is_active' not in existing_sub_cols:
            batch_op.add_column(sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False))

    # ----------------------------------------------------------------
    # 3. Create `plan_features` M2M association table (idempotent)
    # ----------------------------------------------------------------
    existing_tables = sa.inspect(conn).get_table_names()
    if 'plan_features' not in existing_tables:
        op.create_table(
            'plan_features',
            sa.Column('plan_id', sa.Integer(), nullable=False),
            sa.Column('feature_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['plan_id'], ['subscriptions.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['feature_id'], ['features.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('plan_id', 'feature_id'),
        )
        op.create_index('ix_plan_feature', 'plan_features', ['plan_id', 'feature_id'])

    # ----------------------------------------------------------------
    # 4. Create `tenant_feature_overrides` table (idempotent)
    # ----------------------------------------------------------------
    if 'tenant_feature_overrides' not in existing_tables:
        op.create_table(
            'tenant_feature_overrides',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('shop_id', sa.Integer(), nullable=False),
            sa.Column('feature_key', sa.String(length=64), nullable=False),
            sa.Column('is_enabled', sa.Boolean(), nullable=False),
            sa.Column('reason', sa.String(), nullable=True),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ondelete='CASCADE'),
        )
        op.create_index(
            'ix_tenant_feature_override',
            'tenant_feature_overrides',
            ['shop_id', 'feature_key']
        )


def downgrade() -> None:
    # Reverse order of creation
    op.drop_index('ix_tenant_feature_override', table_name='tenant_feature_overrides')
    op.drop_table('tenant_feature_overrides')

    op.drop_index('ix_plan_feature', table_name='plan_features')
    op.drop_table('plan_features')

    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.drop_column('is_active')
        batch_op.drop_column('billing_cycle')

    with op.batch_alter_table('features', schema=None) as batch_op:
        batch_op.drop_column('created_at')
        batch_op.drop_column('is_active')
        batch_op.drop_column('category')
