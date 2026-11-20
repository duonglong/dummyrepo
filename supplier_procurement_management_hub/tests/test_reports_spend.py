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

    def test_fr_0100_price_row_is_per_supplier_and_quarter_with_true_variance(self):
        """FR-SPM-0100: two lines of one supplier give one weighted row and the true variance (-0.95)."""
        from freezegun import freeze_time
        for supplier, price, qty, when in (
                (self.acme, 20.0, 1.0, '2026-08-12 10:00:00'), (self.acme, 10.0, 99.0, '2026-08-13 10:00:00'),
                (self.beta, 12.0, 100.0, '2026-08-14 10:00:00')):
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
        self.assertEqual(len(rows), 2)
        acme = rows.filtered(lambda row: row.partner_id == self.acme)
        self.assertAlmostEqual(acme.price_unit, 10.1, places=2)
        self.assertAlmostEqual(acme.price_variance, -0.95, places=2)

    def test_fr_0100_price_and_variance_are_not_combined_above_the_row(self):
        """FR-SPM-0100: grouped figures carry no mean of prices; only amount and quantity are summed."""
        fields = self.env['procurement.report.price']._fields
        self.assertFalse(fields['price_unit'].aggregator)
        self.assertFalse(fields['price_variance'].aggregator)
        self.assertEqual(fields['amount'].aggregator, 'sum')

    def test_fr_0101_approver_who_approved_does_not_wait(self):
        """FR-SPM-0101: in a step that needs every approver, the one who approved has no waiting age."""
        policy = self.env['procurement.approval.policy'].create({
            'name': 'All policy T31', 'sequence': 1, 'company_id': self.company.id,
            'step_ids': [(0, 0, {
                'name': 'Step', 'sequence': 1, 'approver_type': 'users', 'mode': 'all',
                'user_ids': [(6, 0, (self.manager | self.buyer).ids)]})]})
        self.assertTrue(policy)
        order = self.env['purchase.order'].with_user(self.buyer).create({
            'partner_id': self.acme.id, 'user_id': self.buyer.id,
            'order_line': [(0, 0, {
                'product_id': self.chair.id, 'name': 'Line', 'product_qty': 1.0,
                'price_unit': 100.0, 'tax_ids': [(6, 0, [])]})]})
        from freezegun import freeze_time
        with freeze_time('2026-11-17 09:00:00'):
            order.with_user(self.buyer).action_spm_submit_approval()
        step = self.env['procurement.approval.step'].search([('request_id.order_id', '=', order.id)])
        from ..services import approval
        approval.decide(step.with_user(self.manager), 'approved', '')
        self.env.flush_all()
        rows = self.env['procurement.report.approval'].with_user(self.manager).search([('step_id', '=', step.id)])
        self.assertEqual(rows.filtered(lambda row: row.state == 'open').approver_id, self.buyer)
        self.assertFalse(rows.filtered(lambda row: row.approver_id == self.manager).waiting_hours)
        self.assertTrue(rows.filtered(lambda row: row.approver_id == self.buyer).waiting_hours > 0)
