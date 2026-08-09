"""Convert stock quantity columns from Float to Numeric(10,3)

Menu items sold by weight (e.g. 0.25 kg) were losing precision on repeated
sale deductions because stock_quantity/reserved_quantity/change_qty were
Float (binary floating point) columns with plain Python float arithmetic —
the same class of bug the money columns avoid by using Numeric/Decimal.

Revision ID: b06514b532c9
Revises: b3e7f2a9c451
Create Date: 2026-08-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b06514b532c9'
down_revision: Union[str, Sequence[str], None] = 'b3e7f2a9c451'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('menu_items', schema=None) as batch_op:
        batch_op.alter_column(
            'stock_quantity',
            existing_type=sa.Float(),
            type_=sa.Numeric(10, 3),
            existing_nullable=True,
            postgresql_using='stock_quantity::numeric(10,3)',
        )
        batch_op.alter_column(
            'reserved_quantity',
            existing_type=sa.Float(),
            type_=sa.Numeric(10, 3),
            existing_nullable=False,
            postgresql_using='reserved_quantity::numeric(10,3)',
        )
    with op.batch_alter_table('stock_movements', schema=None) as batch_op:
        batch_op.alter_column(
            'change_qty',
            existing_type=sa.Float(),
            type_=sa.Numeric(10, 3),
            existing_nullable=False,
            postgresql_using='change_qty::numeric(10,3)',
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('stock_movements', schema=None) as batch_op:
        batch_op.alter_column(
            'change_qty',
            existing_type=sa.Numeric(10, 3),
            type_=sa.Float(),
            existing_nullable=False,
        )
    with op.batch_alter_table('menu_items', schema=None) as batch_op:
        batch_op.alter_column(
            'reserved_quantity',
            existing_type=sa.Numeric(10, 3),
            type_=sa.Float(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            'stock_quantity',
            existing_type=sa.Numeric(10, 3),
            type_=sa.Float(),
            existing_nullable=True,
        )
