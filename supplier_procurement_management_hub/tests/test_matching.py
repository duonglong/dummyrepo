"""Unit tests of invoice matching (T-025).

Requirements: FR-SPM-0071, FR-SPM-0072, FR-SPM-0073, BR-SPM-0014
"""
from odoo.tests import tagged

from .matching_common import SpmMatchingCommon


@tagged('post_install', '-at_install')
class TestMatching(SpmMatchingCommon):
    """Requirements: FR-SPM-0071, FR-SPM-0072, FR-SPM-0073, BR-SPM-0014"""

    def setUp(self):
        super().setUp()
        self._clear_config()

    def test_goods_default_to_three_way_without_a_policy(self):
        """Requirements: FR-SPM-0071"""
        bill = self._bill(self._order(receive=100), 100, 11.5)
        self.assertEqual(self._recon(bill).method, 'three_way')

    def test_services_default_to_two_way_without_a_policy(self):
        """Requirements: FR-SPM-0071"""
        service = self.env['product.product'].create({'name': 'Cleaning M5', 'type': 'service'})
        order = self.env['purchase.order'].create({
            'partner_id': self.acme.id,
            'order_line': [(0, 0, {'product_id': service.id, 'name': 'Cleaning',
                                   'product_qty': 1, 'price_unit': 50.0})],
        })
        order.button_confirm()
        bill = self._bill(order, 1, 50.0)
        self.assertEqual(self._recon(bill).method, 'two_way')

    def test_tax_difference_is_recorded_on_the_line(self):
        """Requirements: FR-SPM-0073"""
        order = self._order(receive=100)
        bill = self._bill(order, 100, 11.5)
        bill.invoice_line_ids.tax_ids = self.company_data['default_tax_purchase']
        line = self._recon(bill).line_ids
        self.assertFalse(line.ordered_tax_ids)
        self.assertEqual(line.billed_tax_ids, self.company_data['default_tax_purchase'])
        self.assertEqual(self._recon(bill).state, 'review')

    def test_matched_state_when_everything_agrees(self):
        """Requirements: FR-SPM-0072"""
        self._tolerance('price')
        self._tolerance('quantity')
        bill = self._bill(self._order(receive=100), 100, 11.5)
        self.assertEqual(self._recon(bill).state, 'matched')
        self.assertEqual(bill.spm_match_state, 'matched')

    def test_bill_without_purchase_link_is_unmatched(self):
        """Requirements: FR-SPM-0072"""
        bill = self._plain_bill(self.acme, 100.0)
        self.assertFalse(self._recon(bill))
        self.assertEqual(bill.spm_match_state, 'unmatched')
        found = self.env['account.move'].search([('spm_match_state', '=', 'unmatched'), ('id', '=', bill.id)])
        self.assertEqual(found, bill)

    def test_run_matching_refreshes_on_demand(self):
        """Requirements: FR-SPM-0072"""
        bill = self._bill(self._order(receive=100), 100, 11.5)
        self._recon(bill).line_ids.billed_qty = 5
        self.env['account.move'].browse(bill.id).with_user(self.user_fin).action_spm_run_matching()
        self.assertEqual(self._recon(bill).line_ids.billed_qty, 100)
