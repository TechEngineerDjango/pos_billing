"""Drop legacy Subscription.enabled_features JSON column

Revision ID: d4b9a1e6f732
Revises: ef6da53c4848
Create Date: 2026-08-01 00:00:00.000000

Feature access is now resolved entirely through the M2M plan_features
table (see app/domains/features/service.py::FeatureService). All plans
have been backfilled into plan_features from their prior enabled_features
JSON contents, so the legacy column is no longer read anywhere and can be
dropped.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4b9a1e6f732'
down_revision: Union[str, Sequence[str], None] = 'ef6da53c4848'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    existing_cols = [col['name'] for col in sa.inspect(conn).get_columns('subscriptions')]
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        if 'enabled_features' in existing_cols:
            batch_op.drop_column('enabled_features')


def downgrade() -> None:
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('enabled_features', sa.JSON(), nullable=True))
