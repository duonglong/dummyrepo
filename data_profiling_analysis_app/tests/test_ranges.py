from odoo.tests import TransactionCase, tagged

from ..services import profiler
from ..services.stats import quartiles, usual_range


@tagged('post_install', '-at_install')
class TestRanges(TransactionCase):
    """Quartiles and usual range of numbers."""

    def test_quartiles_inclusive(self):
        """Requirements: BR-DPA-0004 (QUARTILE.INC: linear interpolation)."""
        self.assertEqual(quartiles([10, 10, 11, 11, 12, 12, 12, 13, 13, 14, 500]), (11, 12, 13))
        self.assertEqual(quartiles([1, 2, 3, 4]), (1.75, 2.5, 3.25))

    def test_usual_range_bounds(self):
        """Requirements: BR-DPA-0004 (Q1 - 1.5 IQR to Q3 + 1.5 IQR)."""
        self.assertEqual(usual_range(11, 13), (8, 16))

    def test_value_on_bound_is_usual(self):
        """Requirements: FR-DPA-0010 (a value on a bound is usual; the bound itself is 16)."""
        self.env.cr.execute("UPDATE res_partner SET active = false")
        self.env.invalidate_all()
        values = [10, 10, 11, 11, 12, 12, 12, 13, 13, 14, 16]
        self.env['res.partner'].create([{'name': 'N%d' % i, 'color': v} for i, v in enumerate(values)])
        result = profiler.profile(self.env, 'res.partner', ['color'])
        line = result['fields'][0]
        self.assertEqual((line['range_low'], line['range_high']), (8.0, 16.0))
        self.assertEqual([f['value_label'] for f in line['findings']], [])
