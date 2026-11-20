"""Own tests of the report views of T-031.

Requirements: FR-SPM-0099, FR-SPM-0100, FR-SPM-0101
"""
from .comparison_common import SpmComparisonCase


class TestReportsSpend(SpmComparisonCase):
    """Requirements: FR-SPM-0099, FR-SPM-0100"""

    def test_fr_0100_price_trend_is_empty_without_purchases(self):
        """FR-SPM-0100: a product nobody bought in the period has no price rows."""
        rows = self.env['procurement.report.price'].with_user(self.manager).spm_price_trend(
            self.desk, '2026-07-01', '2026-09-30')
        self.assertEqual(rows, [])

    def test_fr_0099_spend_view_is_read_only_for_a_buyer(self):
        """FR-SPM-0099: a buyer reads the spend rows and holds no write right on them."""
        spend = self.env['procurement.report.spend'].with_user(self.buyer)
        self.assertTrue(spend.has_access('read'))
        self.assertFalse(spend.has_access('write'))

    def test_fr_0100_price_variance_is_on_the_view_and_weighted(self):
        """FR-SPM-0100: each price row carries its variance from the quantity-weighted quarter
        average, and amount over quantity gives the weighted price of a grouping."""
        from freezegun import freeze_time
        for supplier, price, qty, when in (
                (self.acme, 10.0, 30.0, '2026-08-12 10:00:00'), (self.beta, 14.0, 10.0, '2026-08-14 10:00:00')):
            with freeze_time(when):
                order = self.env['purchase.order'].create({
                    'partner_id': supplier.id, 'user_id': self.buyer.id, 'date_order': when,
                    'order_line': [(0, 0, {
                        'product_id': self.desk.id, 'name': 'Line', 'product_qty': qty,
                        'price_unit': price, 'tax_ids': [(6, 0, [])]})]})
                order.button_confirm()
                if order.state != 'purchase':
                    order.button_approve(force=True)
        self.env.flush_all()
        rows = self.env['procurement.report.price'].with_user(self.manager).search([('product_id', '=', self.desk.id)])
        variance = {row.partner_id: row.price_variance for row in rows}
        self.assertAlmostEqual(variance[self.acme], -1.0, places=2)
        self.assertAlmostEqual(variance[self.beta], 3.0, places=2)
        self.assertAlmostEqual(sum(rows.mapped('amount')) / sum(rows.mapped('quantity')), 11.0, places=2)

    def test_fr_0101_approval_cycle_counts_a_request_once(self):
        """FR-SPM-0101: a pending step with two approvers has a waiting age on each row and no cycle."""
        from freezegun import freeze_time
        policy = self.env['procurement.approval.policy'].create({
            'name': 'Cycle policy T31', 'sequence': 1, 'company_id': self.company.id,
            'step_ids': [(0, 0, {
                'name': 'Step', 'sequence': 1, 'approver_type': 'users', 'mode': 'any',
                'user_ids': [(6, 0, (self.manager | self.buyer).ids)]})]})
        self.assertTrue(policy)
        order = self.env['purchase.order'].with_user(self.buyer).create({
            'partner_id': self.acme.id, 'user_id': self.buyer.id,
            'order_line': [(0, 0, {
                'product_id': self.chair.id, 'name': 'Line', 'product_qty': 1.0,
                'price_unit': 100.0, 'tax_ids': [(6, 0, [])]})]})
        with freeze_time('2026-11-17 09:00:00'):
            order.with_user(self.buyer).action_spm_submit_approval()
        self.env.flush_all()
        rows = self.env['procurement.report.approval'].with_user(self.manager).search([('state', '=', 'open')])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.waiting_hours > 0 for row in rows))
        self.assertFalse(any(rows.mapped('cycle_hours')))
