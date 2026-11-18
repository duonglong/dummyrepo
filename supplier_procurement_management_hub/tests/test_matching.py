"""Unit tests of invoice matching (T-025).

Requirements: FR-SPM-0071, FR-SPM-0072, FR-SPM-0073, BR-SPM-0014
"""
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from ..services import reconciliation as matching

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

    # access, failure and size ------------------------------------------------
    def test_bill_saved_by_buyer_or_accountant_is_matched(self):
        """Requirements: FR-SPM-0072"""
        order = self._order(receive=100)
        for user in (self.user_ann, self.user_acc):
            bill = self.env['account.move'].with_user(user).browse(self._bill(order, 100, 11.5).id)
            bill.with_user(user).write({'ref': f'by {user.login}'})
            self.assertEqual(self._recon(bill).line_ids.billed_qty, 100, user.login)

    def test_matching_failure_is_recorded_on_first_match(self):
        """Requirements: FR-SPM-0072"""
        order = self._order(receive=100)
        with patch.object(matching, '_refresh_one', side_effect=ValueError('boom')):
            bill = self._bill(order, 100, 11.5)
        self.assertEqual(self._recon(bill).state, 'error')
        self.assertEqual(bill.spm_match_state, 'error')

    def test_matching_failure_replaces_stale_figures(self):
        """Requirements: FR-SPM-0072"""
        bill = self._bill(self._order(receive=100), 100, 11.5)
        self.assertTrue(self._recon(bill).line_ids)
        with patch.object(matching, '_refresh_one', side_effect=ValueError('boom')):
            bill.with_user(self.user_fin).write({'ref': 'changed'})
        recon = self._recon(bill)
        self.assertEqual(recon.state, 'error')
        self.assertFalse(recon.line_ids)

    def test_finance_removing_the_purchase_link_removes_the_match(self):
        """Requirements: FR-SPM-0072"""
        bill = self._bill(self._order(receive=100), 100, 11.5)
        bill.with_user(self.user_fin).invoice_line_ids.purchase_line_id = False
        self.assertFalse(self._recon(bill))

    def test_finance_cannot_edit_tolerances_or_routes(self):
        """Requirements: BR-SPM-0014"""
        tolerance = self._tolerance('price', percent=2)
        with self.assertRaises(AccessError):
            tolerance.with_user(self.user_fin).write({'percent': 50})
        route = self._route('price')
        with self.assertRaises(AccessError):
            route.with_user(self.user_fin).write({'owner_role': 'buyer'})

    def test_service_line_is_two_way_under_a_company_three_way_policy(self):
        """Requirements: FR-SPM-0071"""
        self._policy('three_way')
        service = self.env['product.product'].create({'name': 'Cleaning M5b', 'type': 'service'})
        order = self.env['purchase.order'].create({
            'partner_id': self.acme.id,
            'order_line': [(0, 0, {'product_id': service.id, 'name': 'Cleaning',
                                   'product_qty': 1, 'price_unit': 50.0})],
        })
        order.button_confirm()
        line = self._recon(self._bill(order, 1, 50.0)).line_ids
        self.assertEqual(line.method, 'two_way')
        self.assertEqual(line.qty_difference, 0.0)
        self.assertTrue(line.qty_within_tolerance)

    def test_absolute_tolerance_is_in_company_currency(self):
        """Requirements: BR-SPM-0014"""
        foreign = self.eur if self.company.currency_id == self.usd else self.usd
        self.env['res.currency.rate'].create({
            'currency_id': foreign.id, 'company_id': self.company.id, 'rate': 10.0,
            'name': fields.Date.from_string('2026-01-01')})
        self._tolerance('price', absolute=5.0)
        order = self._order(qty=60, price=11.0, currency=foreign, receive=60)
        within = self._recon(self._bill(order, 20, 12.0)).line_ids  # 20 foreign = 2 in company currency
        above = self._recon(self._bill(order, 60, 12.0)).line_ids  # 60 foreign = 6 in company currency
        self.assertTrue(within.price_within_tolerance)
        self.assertFalse(above.price_within_tolerance)

    def _price_within(self, qty, price):
        order = self._order(qty=100, price=11.5, receive=100)
        return self._recon(self._bill(order, qty, price)).line_ids.price_within_tolerance

    def test_absolute_tolerance_limit_is_inclusive(self):
        """Requirements: BR-SPM-0014"""
        self._tolerance('price', absolute=2.0)
        self.assertTrue(self._price_within(10, 11.7))
        self.assertFalse(self._price_within(10, 11.71))

    def test_percentage_tolerance_limit_is_inclusive(self):
        """Requirements: BR-SPM-0014"""
        self._tolerance('price', percent=2.0)
        self.assertTrue(self._price_within(100, 11.73))
        self.assertFalse(self._price_within(100, 11.74))

    def test_open_ended_band_has_no_ceiling(self):
        """Requirements: BR-SPM-0014"""
        tolerance = self._tolerance('price', absolute=1e12, amount_to=0.0)
        order = self._order(qty=1, price=5e10, receive=1)
        line = self._recon(self._bill(order, 1, 5e10 + 100)).line_ids
        self.assertTrue(tolerance.exists())
        self.assertTrue(line.price_within_tolerance)

    def test_units_that_cannot_convert_show_no_quantity_difference(self):
        """Requirements: FR-SPM-0073"""
        order = self._order(receive=100)
        bill = self._bill(order, 100, 11.5)
        bill.invoice_line_ids.product_uom_id = self.env.ref('uom.product_uom_kgm')
        line = self._recon(bill).line_ids
        self.assertEqual(line.qty_difference, 0.0)
        self.assertEqual(self._recon(bill).state, 'review')

    def test_bill_save_matches_once_and_reads_rules_once_per_bill(self):
        """Requirements: FR-SPM-0072"""
        order = self._order(qty=1000, receive=1000)
        line = order.order_line
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.acme.id,
            'invoice_date': fields.Date.from_string('2026-11-10'),
            'invoice_line_ids': [Command.create({
                'product_id': line.product_id.id, 'name': 'Chair', 'quantity': 1, 'price_unit': 11.5,
                'purchase_line_id': line.id, 'tax_ids': [Command.set([])]}) for _i in range(200)],
        })
        edits = [Command.update(move_line.id, {'quantity': 2}) for move_line in bill.invoice_line_ids]
        policy_model = type(self.env['procurement.matching.policy'])
        tolerance_model = type(self.env['procurement.matching.tolerance'])
        with patch.object(matching, '_refresh_one', wraps=matching._refresh_one) as refreshed, \
                patch.object(policy_model, 'search', autospec=True, side_effect=policy_model.search) as policies, \
                patch.object(tolerance_model, 'search', autospec=True, side_effect=tolerance_model.search) as tolerances:
            bill.write({'invoice_line_ids': edits})
        self.assertEqual(refreshed.call_count, 1)
        self.assertEqual(policies.call_count, 1)
        self.assertEqual(tolerances.call_count, 1)
        self.assertEqual(len(self._recon(bill).line_ids), 200)

    def test_dropped_discount_is_a_price_difference(self):
        """Requirements: FR-SPM-0073"""
        order = self._order(qty=100, price=11.5, receive=100)
        order.order_line.discount = 10.0
        line = self._recon(self._bill(order, 100, 11.5)).line_ids
        self.assertAlmostEqual(line.ordered_price, 10.35)
        self.assertAlmostEqual(line.price_difference, 1.15)
        self.assertAlmostEqual(line.amount_difference, 115.0)
        self.assertFalse(line.price_within_tolerance)

    def test_discount_kept_on_the_bill_matches(self):
        """Requirements: FR-SPM-0073"""
        order = self._order(qty=100, price=11.5, receive=100)
        order.order_line.discount = 10.0
        bill = self._bill(order, 100, 11.5)
        bill.invoice_line_ids.discount = 10.0
        self.assertEqual(self._recon(bill).line_ids.price_difference, 0.0)

    def test_bill_in_dozens_is_compared_per_ordered_unit(self):
        """Requirements: FR-SPM-0073"""
        order = self._order(qty=120, price=11.5, receive=120)
        bill = self._bill(order, 10, 138.0)
        bill.invoice_line_ids.write({'product_uom_id': self.env.ref('uom.product_uom_dozen').id, 'price_unit': 138.0})
        line = self._recon(bill).line_ids
        self.assertEqual(line.billed_qty, 10)
        self.assertEqual(line.price_difference, 0.0)
        self.assertEqual(line.qty_difference, 0.0)
        self.assertEqual(self._recon(bill).state, 'matched')

    def test_read_only_user_cannot_run_matching(self):
        """Requirements: FR-SPM-0072"""
        bill = self._bill(self._order(receive=100), 100, 11.5)
        with self.assertRaises(AccessError):
            bill.with_user(self.user_acc).action_spm_run_matching()
