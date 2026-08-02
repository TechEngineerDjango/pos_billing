"""
BillingService.format_whatsapp_bill_message's item table is a fixed-width
monospace grid (No/Item/Qty/Price/Tax/Amt columns, one header row, one row
per item) wrapped in a ``` code fence so WhatsApp renders it with real
column alignment instead of proportional-font drift. Long item names wrap
onto continuation lines that stay confined to the Item column — the
Qty/Price/Tax/Amt columns are blank on those lines, so wrapped text never
bleeds into the numeric columns. Currency is shown inline on every figure;
there is no top-of-invoice "Amounts in X" line. These tests pin that
structure so a future edit can't silently break column alignment.
"""
from app.domains.billing.service import BillingService

LINE_WIDTH = BillingService._WA_LINE_WIDTH
NO_W = BillingService._WA_NO_W
ITEM_W = BillingService._WA_ITEM_W
QTY_W = BillingService._WA_QTY_W
PRICE_W = BillingService._WA_PRICE_W
TAX_W = BillingService._WA_TAX_W
AMT_W = BillingService._WA_AMT_W


def _extract_table_lines(message: str) -> list[str]:
    inside = message.split("```")[1]
    return [line for line in inside.strip("\n").split("\n")]


def _item(name="Item", qty=1, unit=None, price=10.0, line_tax=0.0, line_total=10.0):
    return {"name": name, "qty": qty, "unit": unit, "price": price,
            "line_tax": line_tax, "line_total": line_total}


def test_no_top_of_invoice_amounts_in_currency_line():
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0001", items_snapshot=[_item()], total_amount=10.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    assert "Amounts in" not in message


def test_all_column_headers_appear_on_a_single_line():
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0002", items_snapshot=[_item()], total_amount=10.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    header = lines[0]
    assert "No" in header and "Item" in header and "Qty" in header
    assert "Price" in header and "Tax" in header and "Amt" in header
    assert len(header) == LINE_WIDTH


def test_every_row_is_exactly_line_width_and_columns_dont_overlap():
    items = [
        _item(name="Item A", qty=1, price=10.0, line_tax=0.0, line_total=10.0),
        _item(name="Item B", qty=2, price=10.0, line_tax=0.0, line_total=20.0),
    ]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0003", items_snapshot=items, total_amount=30.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    for line in lines:
        assert len(line) == LINE_WIDTH
    # header, sep, row A, row B, sep, subtotal, tax, total = 8 lines (no wrapping needed)
    assert len(lines) == 8
    row_a, row_b = lines[2], lines[3]
    # The Item column region (after No) must hold the name, and the numeric
    # columns must start exactly where the header says they do — i.e. the
    # item text itself never appears inside the Qty/Price/Tax/Amt region.
    item_region_a = row_a[NO_W:NO_W + ITEM_W]
    assert "Item A" in item_region_a
    numeric_region_a = row_a[NO_W + ITEM_W:]
    assert "Item A" not in numeric_region_a
    assert row_a.startswith("1")
    assert row_b.startswith("2")


def test_long_item_name_wraps_confined_to_item_column_not_numeric_columns():
    long_name = "Extra Large Family Combo Meal With Extra Cheese And Fries"
    items = [_item(name=long_name, qty=2, price=100.0, line_tax=20.0, line_total=220.0)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0004", items_snapshot=items, total_amount=220.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    item_rows = lines[2:-4]  # strip header, header-sep, footer-sep, subtotal, tax, total
    # Every word survives (nothing clipped).
    reconstructed = " ".join(l[NO_W:NO_W + ITEM_W].strip() for l in item_rows)
    for word in long_name.split():
        assert word in reconstructed
    # Continuation rows carry no qty/price/tax/amount data.
    first_row, *continuation_rows = item_rows
    assert "₹100.00" in first_row and "₹20.00" in first_row and "₹220.00" in first_row
    for cont in continuation_rows:
        assert len(cont) == LINE_WIDTH
        assert cont[NO_W + ITEM_W:].strip() == ""


def test_item_numbering_is_sequential_starting_at_one():
    items = [_item(name=f"Item {i}") for i in range(3)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0005", items_snapshot=items, total_amount=30.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    assert lines[2].startswith("1")
    assert lines[3].startswith("2")
    assert lines[4].startswith("3")


def test_unit_merged_into_qty_for_kg_liter_and_fractional_values():
    items = [
        _item(name="Rice", qty=2.5, unit="kg", price=40.0, line_total=100.0),
        _item(name="Milk", qty=0.5, unit="liter", price=60.0, line_total=30.0),
        _item(name="Water", qty=8, unit="liter", price=20.0, line_total=160.0),
    ]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0006", items_snapshot=items, total_amount=290.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    assert "2.5 kg" in lines[2]
    assert "0.5 ltr" in lines[3]
    assert "8 ltr" in lines[4]


def test_piece_and_no_unit_items_show_plain_qty_with_no_unit_text():
    items = [
        _item(name="Burger", qty=3, unit="piece", price=100.0, line_total=300.0),
        _item(name="Gift Card", qty=1, unit=None, price=50.0, line_total=50.0),
    ]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0007", items_snapshot=items, total_amount=350.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    assert "piece" not in lines[2] and "each" not in lines[2]
    assert "piece" not in lines[3] and "each" not in lines[3]


def test_tax_and_amount_columns_show_currency_prefix():
    items = [_item(name="Item A", qty=1, price=100.0, line_tax=18.0, line_total=118.0)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0008", items_snapshot=items, total_amount=118.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    assert "₹18.00" in lines[2]
    assert "₹118.00" in lines[2]


def test_currency_symbol_reflects_the_passed_in_currency():
    items = [_item(name="Item A", qty=1, price=10.0, line_tax=0.0, line_total=10.0)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0009", items_snapshot=items, total_amount=10.0,
        shop_name="Demo Shop", currency="$", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    assert "$10.00" in lines[2]
    assert "TOTAL" in lines[-1] and "$10.00" in lines[-1]


def test_subtotal_tax_and_total_shown_as_separate_rows_when_provided():
    items = [_item(name="Item A", qty=1, price=100.0, line_tax=18.0, line_total=118.0)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0011", items_snapshot=items, total_amount=118.0,
        subtotal_amount=100.0, tax_amount=18.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    assert "SUBTOTAL" in lines[-3] and "₹100.00" in lines[-3]
    assert lines[-2].startswith("TAX") and "₹18.00" in lines[-2]
    assert lines[-1].startswith("TOTAL") and "₹118.00" in lines[-1]


def test_subtotal_and_tax_default_from_line_items_when_not_provided():
    items = [
        _item(name="Item A", qty=1, price=100.0, line_tax=18.0, line_total=118.0),
        _item(name="Item B", qty=1, price=50.0, line_tax=0.0, line_total=50.0),
    ]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0012", items_snapshot=items, total_amount=168.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    assert "SUBTOTAL" in lines[-3] and "₹150.00" in lines[-3]
    assert lines[-2].startswith("TAX") and "₹18.00" in lines[-2]


def test_price_over_column_width_still_produces_readable_row():
    """A price with more digits than the column allows must not silently
    corrupt the layout — Python's format spec widens the field rather than
    truncating, which is the correct failure mode."""
    items = [_item(name="Bulk Order", qty=1, price=123456.78, line_tax=0.0, line_total=123456.78)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0010", items_snapshot=items, total_amount=123456.78,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    assert "123456.78" in lines[2]


def test_credit_sale_due_date_line_present_outside_table():
    items = [_item()]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0011", items_snapshot=items, total_amount=10.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
        due_date="09-Aug-2026", payment_status="Unpaid",
    )
    assert "Payment Due: 09-Aug-2026" in message
    for line in _extract_table_lines(message):
        assert "Payment Due" not in line


def test_paid_bill_has_no_due_date_line():
    items = [_item()]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0012", items_snapshot=items, total_amount=10.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
        due_date="09-Aug-2026", payment_status="Paid",
    )
    assert "Payment Due" not in message
