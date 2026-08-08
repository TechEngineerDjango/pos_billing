"""Add delivery_status to Bill and block_over_credit_limit to Shop

Revision ID: b3e7f2a9c451
Revises: a7c3e9f10b21
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3e7f2a9c451'
down_revision: Union[str, Sequence[str], None] = 'a7c3e9f10b21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('bills', schema=None) as batch_op:
        batch_op.add_column(sa.Column('delivery_status', sa.String(), nullable=False, server_default='Pending'))
    with op.batch_alter_table('shops', schema=None) as batch_op:
        batch_op.add_column(sa.Column('block_over_credit_limit', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('shops', schema=None) as batch_op:
        batch_op.drop_column('block_over_credit_limit')
    with op.batch_alter_table('bills', schema=None) as batch_op:
        batch_op.drop_column('delivery_status')
