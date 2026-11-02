from odoo.tests import tagged, TransactionCase


@tagged('post_install', '-at_install')
class TestPosOrderPoints(TransactionCase):
    """Own checks of the point of sale hook."""

    def test_hook_scales_by_weekend_factor(self):
        """Requirements: FR-DLP-003, NFR-REL-001."""
        program = self.env['loyalty.program'].create({
            'name': 'P', 'program_type': 'loyalty', 'weekend_double_points': True})
        self.env.company.tz = 'UTC'
        order = self.env['pos.order'].new({
            'date_order': '2026-11-07 10:00:00', 'company_id': self.env.company.id})
        self.assertEqual(program._get_weekend_factor(order.date_order, order.company_id), 2)
        order.date_order = '2026-11-04 10:00:00'
        self.assertEqual(program._get_weekend_factor(order.date_order, order.company_id), 1)
