"""Pure helpers of the delivery tracking (commitments, schedules, overdue days).

Requirements: BR-SPM-0012, FR-SPM-0056, FR-SPM-0059
"""
from odoo.tools import float_compare, float_is_zero


def agreed(confirmed_date, expected_date):
    """Return the agreed date: the supplier-confirmed date when there is one, else the expected date.

    Requirements: BR-SPM-0012
    """
    return confirmed_date or expected_date


def overdue_days(agreed_day, today, remaining_qty, rounding):
    """Return the days a delivery is late, 0 when nothing remains or the agreed day is not before today.

    Requirements: FR-SPM-0059
    """
    if not agreed_day or float_compare(remaining_qty, 0.0, precision_rounding=rounding) <= 0:
        return 0
    return max((today - agreed_day).days, 0)


def allocate(received_qty, quantities):
    """Spread a received quantity over schedule quantities in order; return one received share per schedule.

    Requirements: FR-SPM-0056
    """
    left = max(received_qty, 0.0)
    shares = []
    for quantity in quantities:
        share = min(left, quantity)
        shares.append(share)
        left -= share
    return shares


def schedules_match(total, ordered, rounding):
    """Return True when the schedule quantities add up to the ordered quantity.

    Requirements: FR-SPM-0056
    """
    return float_is_zero(total - ordered, precision_rounding=rounding)


def split_lines(first_date, second_date, ordered_qty, second_qty):
    """Return the two lines of a split proposal: the rest of the ordered quantity first, then the second part.

    Requirements: FR-SPM-0025
    """
    return [{'date': first_date, 'quantity': ordered_qty - second_qty},
            {'date': second_date, 'quantity': second_qty}]
