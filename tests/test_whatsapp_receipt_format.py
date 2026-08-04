"""
BillingService.format_whatsapp_bill_message's item table is a narrow
fixed-width monospace grid (No/Item/Qty/Amt columns, one header row, one row
per item) wrapped in a ``` code fence. It's kept to ~28 chars because
WhatsApp renders the code block at the recipient's device font size, which
only fits ~28-30 monospace chars on a phone before wrapping — there is no
way to shrink the font from our side, so width is the only lever. Per-item
price/tax are intentionally NOT in the grid (they'd blow the width budget);
they surface in the Subtotal/Tax/Total summary below. Long item names wrap
onto continuation lines confined to the Item column. These tests pin that
structure so a future edit can't silently break the phone-width alignment.
"""
from app.domains.billing.service import BillingService

LINE_WIDTH = BillingService._WA_LINE_WIDTH
GAP = BillingService._WA_GAP
NO_W = BillingService._WA_NO_W
ITEM_W = BillingService._WA_ITEM_W
QTY_W = BillingService._WA_QTY_W
AMT_W = BillingService._WA_AMT_W

# Column start offsets within a row string — columns are GAP-separated, not
# contiguous, so slicing must skip the gap before each column.
ITEM_START = NO_W + len(GAP)
QTY_START = ITEM_START + ITEM_W + len(GAP)
AMT_START = QTY_START + QTY_W + len(GAP)


def _extract_table_lines(message: str) -> list[str]:
    inside = message.split("```")[1]
    return [line for line in inside.strip("\n").split("\n")]


def _item(name="Item", qty=1, unit=None, price=10.0, line_tax=0.0, line_total=10.0):
    return {"name": name, "qty": qty, "unit": unit, "price": price,
            "line_tax": line_tax, "line_total": line_total}


def test_table_fits_phone_width():
    """Every table line must stay within the narrow phone-friendly width so
    WhatsApp doesn't wrap it — regression guard for the exact bug this
    layout exists to prevent."""
    assert LINE_WIDTH <= 30
    items = [_item(name="Item A", qty=1, price=10.0, line_total=10.0)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0001", items_snapshot=items, total_amount=10.0,
        subtotal_amount=10.0, tax_amount=0.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    for line in _extract_table_lines(message):
        assert len(line) == LINE_WIDTH


def test_no_top_of_invoice_amounts_in_currency_line():
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0002", items_snapshot=[_item()], total_amount=10.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    assert "Amounts in" not in message


def test_all_column_headers_appear_on_a_single_line():
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0003", items_snapshot=[_item()], total_amount=10.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    header = lines[0]
    assert "No" in header and "Item" in header and "Qty" in header and "Amt" in header
    assert len(header) == LINE_WIDTH


def test_per_item_price_and_tax_not_in_grid_rows():
    """Price and Tax are shown only in the summary, not per item row —
    dropping them is what keeps the grid within phone width."""
    items = [_item(name="Item A", qty=1, price=100.0, line_tax=18.0, line_total=118.0)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0004", items_snapshot=items, total_amount=118.0,
        subtotal_amount=100.0, tax_amount=18.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    item_row = lines[2]
    # The item row shows the line Amount (₹118.00) but not the per-unit price
    # (₹100.00) or the per-line tax (₹18.00).
    assert "₹118.00" in item_row
    assert "₹100.00" not in item_row
    assert "₹18.00" not in item_row


def test_item_row_columns_dont_overlap():
    items = [
        _item(name="Item A", qty=1, price=10.0, line_total=10.0),
        _item(name="Item B", qty=2, price=10.0, line_total=20.0),
    ]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0005", items_snapshot=items, total_amount=30.0,
        subtotal_amount=30.0, tax_amount=0.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    # header, sep, row A, row B, sep, subtotal, tax, total = 8 lines
    assert len(lines) == 8
    row_a = lines[2]
    assert "Item A" in row_a[ITEM_START:ITEM_START + ITEM_W]
    assert "Item A" not in row_a[QTY_START:]  # name never bleeds into numeric cols
    assert row_a.startswith("1")
    assert lines[3].startswith("2")


def test_long_item_name_wraps_confined_to_item_column():
    long_name = "Extra Large Family Combo Meal With Cheese"
    items = [_item(name=long_name, qty=2, price=100.0, line_total=220.0)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0006", items_snapshot=items, total_amount=220.0,
        subtotal_amount=220.0, tax_amount=0.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    item_rows = lines[2:-4]  # strip header, header-sep, footer-sep, subtotal, tax, total
    reconstructed = " ".join(l[ITEM_START:ITEM_START + ITEM_W].strip() for l in item_rows)
    for word in long_name.split():
        assert word in reconstructed
    # First row carries the amount; continuation rows carry no qty/amount.
    first_row, *continuation_rows = item_rows
    assert "₹220.00" in first_row
    for cont in continuation_rows:
        assert len(cont) == LINE_WIDTH
        assert cont[QTY_START:].strip() == ""


def test_item_numbering_is_sequential_starting_at_one():
    items = [_item(name=f"It{i}") for i in range(3)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0007", items_snapshot=items, total_amount=30.0,
        subtotal_amount=30.0, tax_amount=0.0,
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
        bill_number="B-0008", items_snapshot=items, total_amount=290.0,
        subtotal_amount=290.0, tax_amount=0.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    assert "2.5 kg" in lines[2]
    assert "0.5 ltr" in lines[3]
    assert "8 ltr" in lines[4]


def test_piece_and_no_unit_items_show_plain_qty_with_no_unit_text():
    items = [
        _item(name="Burger", qty=3, unit="piece", price=100.0, line_total=300.0),
        _item(name="Card", qty=1, unit=None, price=50.0, line_total=50.0),
    ]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0009", items_snapshot=items, total_amount=350.0,
        subtotal_amount=350.0, tax_amount=0.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    assert "piece" not in lines[2] and "each" not in lines[2]
    assert "piece" not in lines[3] and "each" not in lines[3]


def test_four_digit_amount_still_fits_without_overlap():
    """A 4-digit line amount (₹1050.00 = 8 chars) fills the Amt column
    exactly but the single-space gap before it keeps it off the Qty
    column."""
    items = [_item(name="Bulk", qty=3, price=350.0, line_total=1050.00)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0010", items_snapshot=items, total_amount=1050.00,
        subtotal_amount=1050.00, tax_amount=0.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    item_row = lines[2]
    assert len(item_row) == LINE_WIDTH
    assert item_row[AMT_START - len(GAP):AMT_START] == GAP  # gap before Amt
    assert item_row[AMT_START:].strip() == "₹1050.00"


def test_currency_symbol_reflects_the_passed_in_currency():
    items = [_item(name="Item A", qty=1, price=10.0, line_total=10.0)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0011", items_snapshot=items, total_amount=10.0,
        subtotal_amount=10.0, tax_amount=0.0,
        shop_name="Demo Shop", currency="$", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    assert "$10.00" in lines[2]
    assert "TOTAL" in lines[-1] and "$10.00" in lines[-1]


def test_subtotal_tax_and_total_shown_as_separate_rows_when_provided():
    items = [_item(name="Item A", qty=1, price=100.0, line_tax=18.0, line_total=118.0)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0012", items_snapshot=items, total_amount=118.0,
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
        bill_number="B-0013", items_snapshot=items, total_amount=168.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    assert "SUBTOTAL" in lines[-3] and "₹150.00" in lines[-3]
    assert lines[-2].startswith("TAX") and "₹18.00" in lines[-2]


def test_delivery_row_shown_when_delivery_charge_positive():
    items = [_item(name="Item A", qty=1, price=100.0, line_total=100.0)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0014", items_snapshot=items, total_amount=145.0,
        subtotal_amount=100.0, tax_amount=0.0, delivery_charge=45.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    lines = _extract_table_lines(message)
    assert lines[-2].startswith("DELIVERY") and "₹45.00" in lines[-2]
    assert lines[-1].startswith("TOTAL") and "₹145.00" in lines[-1]


def test_delivery_row_absent_when_no_delivery_charge():
    items = [_item(name="Item A", qty=1, price=100.0, line_total=100.0)]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0015", items_snapshot=items, total_amount=100.0,
        subtotal_amount=100.0, tax_amount=0.0, delivery_charge=0.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
    )
    assert "DELIVERY" not in message
    assert _extract_table_lines(message)[-1].startswith("TOTAL")


def test_credit_sale_due_date_line_present_outside_table():
    items = [_item()]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0016", items_snapshot=items, total_amount=10.0,
        subtotal_amount=10.0, tax_amount=0.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
        due_date="09-Aug-2026", payment_status="Unpaid",
    )
    assert "Payment Due: 09-Aug-2026" in message
    for line in _extract_table_lines(message):
        assert "Payment Due" not in line


def test_paid_bill_has_no_due_date_line():
    items = [_item()]
    message = BillingService.format_whatsapp_bill_message(
        bill_number="B-0017", items_snapshot=items, total_amount=10.0,
        subtotal_amount=10.0, tax_amount=0.0,
        shop_name="Demo Shop", currency="₹", bill_date="02-Aug-2026",
        due_date="09-Aug-2026", payment_status="Paid",
    )
    assert "Payment Due" not in message
