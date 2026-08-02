"""reconcile credit billing schema and add expenses table

Revision ID: c0fb54c1a8ad
Revises: c4e0ebf302d8
Create Date: 2026-07-15 12:03:06.474710

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c0fb54c1a8ad'
down_revision: Union[str, Sequence[str], None] = 'c4e0ebf302d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Reconciles this session's credit-billing rebuild against whatever drift
    already exists (see c4e0ebf302d8's own idempotency guard — this repo's
    dev-mode Base.metadata.create_all-on-boot means tables can appear
    out-of-band before a migration formalizes them). Every operation is
    guarded so this is safe to run against a clean DB, the old ad-hoc
    schema, or a DB where some pieces already landed out-of-band.

    Ordering: credit_statements must exist before bills.credit_statement_id
    can reference it. credit_notes (BACKLOG-2, not requested) and the old
    FIFO-per-bill credit_payments shape are dropped — no real customer data
    exists in either yet, this feature is unreleased.
    """
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())

    # 1. credit_statements (FEAT-5) — must exist before bills.credit_statement_id
    if 'credit_statements' not in existing_tables:
        op.create_table(
            'credit_statements',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('slug', sa.String(length=36), nullable=True),
            sa.Column('shop_id', sa.Integer(), nullable=False),
            sa.Column('customer_id', sa.Integer(), nullable=False),
            sa.Column('statement_number', sa.String(), nullable=False),
            sa.Column('period_start', sa.Date(), nullable=False),
            sa.Column('period_end', sa.Date(), nullable=False),
            sa.Column('total_amount', sa.Numeric(precision=10, scale=2), nullable=False),
            sa.Column('amount_paid', sa.Numeric(precision=10, scale=2), server_default='0.00', nullable=True),
            sa.Column('status', sa.String(), server_default='Open', nullable=True),
            sa.Column('due_date', sa.Date(), nullable=True),
            sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        with op.batch_alter_table('credit_statements', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_credit_statements_id'), ['id'], unique=False)
            batch_op.create_index(batch_op.f('ix_credit_statements_slug'), ['slug'], unique=True)
            batch_op.create_index(batch_op.f('ix_credit_statements_customer_id'), ['customer_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_credit_statements_status'), ['status'], unique=False)
            batch_op.create_index('ix_credit_statements_customer_status', ['customer_id', 'status'], unique=False)
        existing_tables.add('credit_statements')

    # 2. customers.next_statement_date (FEAT-2/7 — cron worker filters on this)
    customer_cols = {c['name'] for c in inspector.get_columns('customers')}
    if 'next_statement_date' not in customer_cols:
        with op.batch_alter_table('customers', schema=None) as batch_op:
            batch_op.add_column(sa.Column('next_statement_date', sa.Date(), nullable=True))
            batch_op.create_index(
                batch_op.f('ix_customers_next_statement_date'), ['next_statement_date'], unique=False
            )

    # 3. bills.credit_statement_id (FEAT-4/5/7)
    bill_cols = {c['name'] for c in inspector.get_columns('bills')}
    if 'credit_statement_id' not in bill_cols:
        with op.batch_alter_table('bills', schema=None) as batch_op:
            batch_op.add_column(sa.Column('credit_statement_id', sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                'fk_bills_credit_statement_id', 'credit_statements', ['credit_statement_id'], ['id'],
                ondelete='SET NULL',
            )
            batch_op.create_index(
                batch_op.f('ix_bills_credit_statement_id'), ['credit_statement_id'], unique=False
            )

    # 4. customer_item_prices (FEAT-3)
    if 'customer_item_prices' not in existing_tables:
        op.create_table(
            'customer_item_prices',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('slug', sa.String(length=36), nullable=True),
            sa.Column('shop_id', sa.Integer(), nullable=False),
            sa.Column('customer_id', sa.Integer(), nullable=False),
            sa.Column('menu_item_id', sa.Integer(), nullable=False),
            sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=False),
            sa.Column('valid_from', sa.Date(), nullable=False),
            sa.Column('valid_to', sa.Date(), nullable=True),
            sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_by_user_id', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['menu_item_id'], ['menu_items.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
        )
        with op.batch_alter_table('customer_item_prices', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_customer_item_prices_id'), ['id'], unique=False)
            batch_op.create_index(batch_op.f('ix_customer_item_prices_slug'), ['slug'], unique=True)
            batch_op.create_index(batch_op.f('ix_customer_item_prices_customer_id'), ['customer_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_customer_item_prices_menu_item_id'), ['menu_item_id'], unique=False)
            batch_op.create_index(
                'ix_cust_item_price_customer_item', ['customer_id', 'menu_item_id'], unique=False
            )

    # 5. drop credit_notes — BACKLOG-2 (cancel/return via CreditNote), not
    # requested; this session's ad-hoc build of it was discarded.
    if 'credit_notes' in existing_tables:
        with op.batch_alter_table('credit_notes', schema=None) as batch_op:
            batch_op.drop_index('ix_credit_notes_bill_created')
            batch_op.drop_index(batch_op.f('ix_credit_notes_id'))
            batch_op.drop_index(batch_op.f('ix_credit_notes_slug'))
        op.drop_table('credit_notes')

    # 6. drop and recreate credit_payments — the ad-hoc FIFO-per-bill shape
    # (customer_id, amount, method, note — no statement/bill FK, no
    # idempotency_key) is replaced with the FEAT-5 shape. This is a lossy
    # operation with no backfill (the old shape has no statement/bill FK to
    # map rows onto), safe only because this feature is unreleased — guard
    # against running it anywhere real payment rows might already exist.
    credit_payments_ok = False
    if 'credit_payments' in existing_tables:
        cp_cols = {c['name'] for c in inspector.get_columns('credit_payments')}
        if 'idempotency_key' in cp_cols:
            credit_payments_ok = True
        else:
            row_count = op.get_bind().execute(sa.text('SELECT COUNT(*) FROM credit_payments')).scalar()
            if row_count:
                raise RuntimeError(
                    f"credit_payments has {row_count} row(s) in the old ad-hoc shape (no idempotency_key). "
                    "This migration drops and recreates the table with no data migration path — "
                    "back up and manually reconcile these rows before re-running upgrade()."
                )
            op.drop_table('credit_payments')

    if not credit_payments_ok:
        op.create_table(
            'credit_payments',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('slug', sa.String(length=36), nullable=True),
            sa.Column('shop_id', sa.Integer(), nullable=False),
            sa.Column('customer_id', sa.Integer(), nullable=False),
            sa.Column('credit_statement_id', sa.Integer(), nullable=True),
            sa.Column('bill_id', sa.Integer(), nullable=True),
            sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
            sa.Column('payment_method', sa.String(length=16), nullable=False),
            sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('recorded_by_user_id', sa.Integer(), nullable=True),
            sa.Column('note', sa.String(), nullable=True),
            sa.Column('idempotency_key', sa.String(length=36), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['credit_statement_id'], ['credit_statements.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['recorded_by_user_id'], ['users.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        with op.batch_alter_table('credit_payments', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_credit_payments_id'), ['id'], unique=False)
            batch_op.create_index(batch_op.f('ix_credit_payments_slug'), ['slug'], unique=True)
            batch_op.create_index(
                batch_op.f('ix_credit_payments_credit_statement_id'), ['credit_statement_id'], unique=False
            )
            batch_op.create_index(
                batch_op.f('ix_credit_payments_idempotency_key'), ['idempotency_key'], unique=True
            )
            batch_op.create_index(
                'ix_credit_payments_customer_created', ['customer_id', 'created_at'], unique=False
            )

    # 7. expenses (FEAT-8)
    if 'expenses' not in existing_tables:
        op.create_table(
            'expenses',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('slug', sa.String(length=36), nullable=True),
            sa.Column('shop_id', sa.Integer(), nullable=False),
            sa.Column('category', sa.String(), nullable=False),
            sa.Column('description', sa.String(), nullable=False),
            sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
            sa.Column('tax_amount', sa.Numeric(precision=10, scale=2), server_default='0.00', nullable=True),
            sa.Column('vendor_name', sa.String(), nullable=True),
            sa.Column('expense_date', sa.Date(), nullable=False),
            sa.Column('payment_method', sa.String(), nullable=False),
            sa.Column('created_by_user_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('is_voided', sa.Boolean(), server_default=sa.text('false'), nullable=True),
            sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        with op.batch_alter_table('expenses', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_expenses_id'), ['id'], unique=False)
            batch_op.create_index(batch_op.f('ix_expenses_slug'), ['slug'], unique=True)
            batch_op.create_index('ix_expenses_shop_category', ['shop_id', 'category'], unique=False)
            batch_op.create_index('ix_expenses_shop_date', ['shop_id', 'expense_date'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('expenses', schema=None) as batch_op:
        batch_op.drop_index('ix_expenses_shop_date')
        batch_op.drop_index('ix_expenses_shop_category')
        batch_op.drop_index(batch_op.f('ix_expenses_slug'))
        batch_op.drop_index(batch_op.f('ix_expenses_id'))
    op.drop_table('expenses')

    # Same data-loss guard as upgrade(): the old FIFO-per-bill shape has no
    # statement_id/idempotency_key to preserve, so refuse to downgrade over
    # real payment rows rather than silently destroying them.
    row_count = op.get_bind().execute(sa.text('SELECT COUNT(*) FROM credit_payments')).scalar()
    if row_count:
        raise RuntimeError(
            f"credit_payments has {row_count} row(s) in the FEAT-5 shape. Downgrading recreates the old "
            "ad-hoc shape with no data migration path — back up and manually reconcile before downgrading."
        )

    with op.batch_alter_table('credit_payments', schema=None) as batch_op:
        batch_op.drop_index('ix_credit_payments_customer_created')
        batch_op.drop_index(batch_op.f('ix_credit_payments_idempotency_key'))
        batch_op.drop_index(batch_op.f('ix_credit_payments_credit_statement_id'))
        batch_op.drop_index(batch_op.f('ix_credit_payments_slug'))
        batch_op.drop_index(batch_op.f('ix_credit_payments_id'))
    op.drop_table('credit_payments')

    op.create_table(
        'credit_payments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.String(length=36), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('shop_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('method', sa.String(length=16), nullable=False),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('recorded_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['recorded_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('credit_payments', schema=None) as batch_op:
        batch_op.create_index('ix_credit_payments_customer_created', ['customer_id', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_credit_payments_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_credit_payments_slug'), ['slug'], unique=True)

    op.create_table(
        'credit_notes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.String(length=36), nullable=True),
        sa.Column('original_bill_id', sa.Integer(), nullable=False),
        sa.Column('shop_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('items_snapshot', sa.JSON(), nullable=False),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['original_bill_id'], ['bills.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('credit_notes', schema=None) as batch_op:
        batch_op.create_index('ix_credit_notes_bill_created', ['original_bill_id', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_credit_notes_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_credit_notes_slug'), ['slug'], unique=True)

    with op.batch_alter_table('customer_item_prices', schema=None) as batch_op:
        batch_op.drop_index('ix_cust_item_price_customer_item')
        batch_op.drop_index(batch_op.f('ix_customer_item_prices_menu_item_id'))
        batch_op.drop_index(batch_op.f('ix_customer_item_prices_customer_id'))
        batch_op.drop_index(batch_op.f('ix_customer_item_prices_slug'))
        batch_op.drop_index(batch_op.f('ix_customer_item_prices_id'))
    op.drop_table('customer_item_prices')

    with op.batch_alter_table('bills', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_bills_credit_statement_id'))
        batch_op.drop_constraint('fk_bills_credit_statement_id', type_='foreignkey')
        batch_op.drop_column('credit_statement_id')

    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_customers_next_statement_date'))
        batch_op.drop_column('next_statement_date')

    with op.batch_alter_table('credit_statements', schema=None) as batch_op:
        batch_op.drop_index('ix_credit_statements_customer_status')
        batch_op.drop_index(batch_op.f('ix_credit_statements_status'))
        batch_op.drop_index(batch_op.f('ix_credit_statements_customer_id'))
        batch_op.drop_index(batch_op.f('ix_credit_statements_slug'))
        batch_op.drop_index(batch_op.f('ix_credit_statements_id'))
    op.drop_table('credit_statements')
