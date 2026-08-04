"""Add delivery_charge to Bill

Revision ID: a7c3e9f10b21
Revises: d4b9a1e6f732
Create Date: 2026-08-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f10b21'
down_revision: Union[str, Sequence[str], None] = 'd4b9a1e6f732'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('bills', schema=None) as batch_op:
        batch_op.add_column(sa.Column('delivery_charge', sa.Numeric(10, 2), nullable=False, server_default='0.00'))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('bills', schema=None) as batch_op:
        batch_op.drop_column('delivery_charge')
