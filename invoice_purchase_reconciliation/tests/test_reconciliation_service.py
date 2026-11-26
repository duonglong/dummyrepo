from odoo.tests import BaseCase, tagged

from ..services import reconciliation

TOLERANCES = {'price_type': 'percent', 'price_value': 5.0, 'qty_type': 'fixed', 'qty_value': 2.0}


def _line(key, ordered_price=10.0, billed_price=10.0, received=100.0, billed=100.0, **extra):
    vals = {
        'key': key, 'on_order': True, 'first_of_order_line': True,
        'ordered_qty': 100.0, 'received_qty': received, 'billed_qty': billed,
        'ordered_price': ordered_price, 'billed_price': billed_price}
    vals.update(extra)
    return vals


@tagged('post_install', '-at_install')
class TestReconciliationService(BaseCase):
    """Unit tests of the check service, without a database.

    Requirements: BR-IPR-0001, FR-IPR-0003, FR-IPR-0004, FR-IPR-0005, FR-IPR-0010
    """

    def test_percent_boundary_in_floats(self):
        """BR-IPR-0001: 10.30 on 10.00 at 3 percent is exactly the limit despite float noise."""
        self.assertTrue(reconciliation.within_tolerance(10.30 - 10.0, 10.0, 'percent', 3.0))
        self.assertFalse(reconciliation.within_tolerance(10.31 - 10.0, 10.0, 'percent', 3.0))

    def test_compare_price_exception(self):
        """FR-IPR-0003: billed 10.51 against 10.00 at 5 percent is a price exception of 0.51."""
        rows, exceptions = reconciliation.compare([_line(1, billed_price=10.51)], TOLERANCES)
        self.assertAlmostEqual(rows[0]['price_difference'], 0.51)
        self.assertEqual(exceptions, [{'kind': 'price', 'key': 1}])
        self.assertEqual(reconciliation.verdict(rows, []), 'waiting')

    def test_compare_quantity_only_over_received(self):
        """FR-IPR-0004: 102 against 100 received within 2 units; 103 is an exception; 50 against 80 is not."""
        _rows, within = reconciliation.compare([_line(1, billed=102.0)], TOLERANCES)
        self.assertFalse(within)
        _rows, over = reconciliation.compare([_line(1, billed=103.0)], TOLERANCES)
        self.assertEqual(over, [{'kind': 'quantity', 'key': 1}])
        _rows, under = reconciliation.compare([_line(1, received=80.0, billed=50.0)], TOLERANCES)
        self.assertFalse(under)

    def test_quantity_exception_once_per_order_line(self):
        """FR-IPR-0004: two bill lines of one order line record the quantity exception once."""
        lines = [_line(1, billed=110.0), _line(2, billed=110.0, first_of_order_line=False)]
        _rows, exceptions = reconciliation.compare(lines, TOLERANCES)
        self.assertEqual(exceptions, [{'kind': 'quantity', 'key': 1}])

    def test_line_not_on_order(self):
        """FR-IPR-0005: a line without order line passes with the result not_on_order."""
        line = _line(1, on_order=False, billed_price=25.0, ordered_price=0.0)
        rows, exceptions = reconciliation.compare([line], TOLERANCES)
        self.assertEqual(rows[0]['result'], 'not_on_order')
        self.assertFalse(exceptions)
        self.assertEqual(reconciliation.verdict(rows, []), 'matched')

    def test_verdict_duplicate_waits(self):
        """FR-IPR-0010: a duplicate alone makes the bill wait."""
        self.assertEqual(reconciliation.verdict([], [1]), 'waiting')
