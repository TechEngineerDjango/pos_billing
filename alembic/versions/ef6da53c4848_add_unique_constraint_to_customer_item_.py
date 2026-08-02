"""add unique constraint to customer_item_prices

Revision ID: ef6da53c4848
Revises: c0fb54c1a8ad
Create Date: 2026-07-18 17:41:24.008831

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ef6da53c4848'
down_revision: Union[str, Sequence[str], None] = 'c0fb54c1a8ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Closes a race in CustomerService.set_item_price: its duplicate-valid_from
    check was an unlocked SELECT, so two concurrent requests setting the same
    rate-card price for the same (customer, item, date) could both pass and
    both insert, leaving two rows with an ambiguous "which one wins" order.
    This constraint makes that impossible at the DB level; the app layer
    catches the resulting IntegrityError and turns it into the same
    ValueError the pre-existing check already raises.
    """
    inspector = sa.inspect(op.get_bind())
    existing_constraints = {
        c["name"] for c in inspector.get_unique_constraints("customer_item_prices")
    }
    if "uq_customer_item_price_customer_item_valid_from" not in existing_constraints:
        with op.batch_alter_table("customer_item_prices", schema=None) as batch_op:
            batch_op.create_unique_constraint(
                "uq_customer_item_price_customer_item_valid_from",
                ["customer_id", "menu_item_id", "valid_from"],
            )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("customer_item_prices", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_customer_item_price_customer_item_valid_from", type_="unique"
        )
