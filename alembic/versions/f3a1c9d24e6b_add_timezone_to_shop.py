"""Add timezone to Shop

Revision ID: f3a1c9d24e6b
Revises: ce8f7a9b1c2d
Create Date: 2026-07-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a1c9d24e6b'
down_revision: Union[str, Sequence[str], None] = 'ce8f7a9b1c2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('shops', schema=None) as batch_op:
        batch_op.add_column(sa.Column('timezone', sa.String(), nullable=False, server_default='Asia/Kolkata'))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('shops', schema=None) as batch_op:
        batch_op.drop_column('timezone')
