"""Shared names of the reconciliation status and history events.

Requirements: FR-IPR-0020
"""

from odoo.tools import float_compare

STATES = [
    ('not_checked', 'Not checked'),
    ('matched', 'Matched'),
    ('waiting', 'Waiting for approval'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
]

EVENTS = [
    ('check', 'Check'),
    ('approve', 'Approve'),
    ('reject', 'Reject'),
    ('reset', 'Reset'),
    ('delete', 'Deleted'),
]


PRECISION_DIGITS = 6


def within_tolerance(difference: float, reference: float, kind: str, value: float) -> bool:
    """Tell whether a difference is within the tolerance; equality is accepted.

    Requirements: BR-IPR-0001
    """
    limit = value if kind == 'fixed' else abs(reference) * value / 100
    return float_compare(abs(difference), limit, precision_digits=PRECISION_DIGITS) <= 0


def compare(lines: list[dict], tolerances: dict) -> tuple[list[dict], list[dict]]:
    """Compare bill lines with their order lines.

    Each line is a dict with `key`, `on_order`, `first_of_order_line` and the
    ordered, received and billed quantities and prices. The tolerances hold
    `price_type`, `price_value`, `qty_type` and `qty_value`, the fixed price
    already in the bill currency. Returns the rows and the exceptions.

    Requirements: FR-IPR-0001, FR-IPR-0003, FR-IPR-0004, FR-IPR-0005, FR-IPR-0006
    """
    rows = []
    exceptions = []
    for line in lines:
        row = dict(line, price_difference=0.0, qty_difference=0.0,
                   price_exception=False, qty_exception=False, result='not_on_order')
        rows.append(row)
        if not line['on_order']:
            continue
        row['price_difference'] = line['billed_price'] - line['ordered_price']
        row['qty_difference'] = line['billed_qty'] - line['received_qty']
        row['price_exception'] = not within_tolerance(
            row['price_difference'], line['ordered_price'],
            tolerances['price_type'], tolerances['price_value'])
        row['qty_exception'] = line['first_of_order_line'] and row['qty_difference'] > 0 and (
            not within_tolerance(
                row['qty_difference'], line['received_qty'],
                tolerances['qty_type'], tolerances['qty_value']))
        row['result'] = 'exception' if row['price_exception'] or row['qty_exception'] else 'matched'
        for kind, flag in (('price', 'price_exception'), ('quantity', 'qty_exception')):
            if row[flag]:
                exceptions.append({'kind': kind, 'key': line['key']})
    return rows, exceptions


def verdict(rows: list[dict], duplicates: list) -> str:
    """Return 'matched' when nothing is an exception, else 'waiting'.

    Requirements: FR-IPR-0010
    """
    has_exception = any(row['result'] == 'exception' for row in rows)
    return 'waiting' if has_exception or duplicates else 'matched'
