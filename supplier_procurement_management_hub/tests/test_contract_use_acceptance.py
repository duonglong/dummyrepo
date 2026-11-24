"""Acceptance tests of T-022 (contract lifecycle and use).

Requirements: BR-SPM-0011, FR-SPM-0066, FR-SPM-0067, FR-SPM-0068, FR-SPM-0069, FR-SPM-0070

Written from the specification. Names not fixed by the design are in
contract_common. The renewal and revision dialogs are left to the build: the
tests call the model methods. The vendor price preview is a transient dialog
opened by action_spm_update_vendor_prices and driven with Form.
"""
from datetime import date, timedelta

from freezegun import freeze_time

from odoo.tests import Form

from .contract_common import SpmContractCase


class TestContractUseAcceptance(SpmContractCase):

    # helpers ---------------------------------------------------------------
    def _order(self, partner, qty, price, planned=None, confirm=False, user=None):
        order = self.env['purchase.order'].with_user(user or self.ann).create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {
                'product_id': self.chair.id, 'name': 'Chair', 'product_qty': qty,
                'price_unit': price, 'tax_ids': [(6, 0, [])],
                'date_planned': planned or '2030-06-01 08:00:00',
            })],
        })
        if confirm:
            order.button_confirm()
        return order

    def _receive(self, order, qty):
        picking = order.picking_ids[:1]
        picking.move_ids.quantity = qty
        picking.move_ids.picked = True
        picking.with_context(skip_backorder=True,
                             picking_ids_not_to_backorder=picking.ids).button_validate()
        return picking

    def _return(self, picking, qty):
        ret = picking._create_return()
        ret.move_ids.write({'quantity': qty, 'to_refund': True})
        ret.move_ids.picked = True
        ret.with_context(skip_backorder=True,
                         picking_ids_not_to_backorder=ret.ids).button_validate()

    # FR-SPM-0066 -----------------------------------------------------------
    def test_sc_0066_1_notice_date_and_expiry(self):
        """Requirements: FR-SPM-0066. SC-SPM-0066-1: ending 2027-12-31, 60-day notice:
        notice date 2027-11-01; the daily job on 2028-01-01 with no renewal makes it Expired."""
        contract = self._active_contract(start='2026-06-01', end='2027-12-31', notice=60)
        self.assertEqual(contract.notice_date, date(2027, 11, 1))
        with freeze_time('2027-12-31 12:00:00'):
            self.env['procurement.contract']._spm_update_states()
        self.assertEqual(contract.state, 'active', "the end date itself is still inside the contract")
        with freeze_time('2028-01-01 02:00:00'):
            self.env['procurement.contract']._spm_update_states()
        self.assertEqual(contract.state, 'expired')

    def test_sc_0066_2_renewal_is_a_new_version_and_stays_active(self):
        """Requirements: FR-SPM-0066. SC-SPM-0066-2: the owner renews to 2028-12-31."""
        contract = self._active_contract(start='2026-06-01', end='2027-12-31', notice=60)
        contract.with_user(self.ann).action_spm_renew('2028-12-31')
        self.assertEqual(contract.state, 'active')
        self.assertEqual(contract.version, 2)
        self.assertEqual(contract.date_end, date(2028, 12, 31))
        self.assertEqual(contract.notice_date, date(2028, 11, 1))
        renewal = contract.version_ids.filtered(lambda v: v.kind == 'renewal')
        self.assertEqual(len(renewal), 1)
        with freeze_time('2028-01-01 02:00:00'):
            self.env['procurement.contract']._spm_update_states()
        self.assertEqual(contract.state, 'active', "a renewed contract does not expire at the old end date")

    # FR-SPM-0067 -----------------------------------------------------------
    def _supplierinfo(self, price=12.0):
        return self.env['product.supplierinfo'].create({
            'partner_id': self.acme.id, 'product_tmpl_id': self.chair.product_tmpl_id.id,
            'product_id': self.chair.id, 'price': price, 'min_qty': 20.0, 'delay': 5,
            'company_id': self.company.id,
        })

    def _price_dialog(self, contract):
        action = contract.with_user(self.bob).action_spm_update_vendor_prices()
        ctx = dict(action.get('context') or {})
        return Form(self.env[action['res_model']].with_user(self.bob).with_context(**ctx)).save()

    def test_sc_0067_1_preview_then_confirm_updates_vendor_price(self):
        """Requirements: FR-SPM-0067. SC-SPM-0067-1: chairs 11.50, minimum 20, 10 days, valid 2027;
        Acme's vendor price is 12.00. The preview shows 12.00 to 11.50; only confirming changes it."""
        info = self._supplierinfo(12.0)
        contract = self._active_contract(start='2027-01-01', end='2027-12-31', notice=30,
                                         lines=[self._line_vals(price=11.5)])
        dialog = self._price_dialog(contract)
        self.assertEqual(len(dialog.line_ids), 1)
        row = dialog.line_ids
        self.assertEqual(row.operation, 'update')
        self.assertEqual(row.old_price, 12.0)
        self.assertEqual(row.new_price, 11.5)
        self.assertEqual(info.price, 12.0, "the preview changes nothing")
        dialog.with_user(self.bob).action_confirm()
        info.invalidate_recordset()
        self.assertEqual(info.price, 11.5)
        self.assertEqual(info.min_qty, 20.0)
        self.assertEqual(info.delay, 10)
        self.assertEqual(info.date_start, date(2027, 1, 1))
        self.assertEqual(info.date_end, date(2027, 12, 31))
        self.assertEqual(info.spm_contract_id, contract)
        self.assertEqual(info.spm_contract_line_id, contract.line_ids)

    def test_fr_0067_missing_vendor_price_is_created_on_confirmation(self):
        """Requirements: FR-SPM-0067: no vendor price yet: the preview says create, confirming creates it."""
        contract = self._active_contract(start='2027-01-01', end='2027-12-31',
                                         lines=[self._line_vals(price=11.5)])
        dialog = self._price_dialog(contract)
        self.assertEqual(dialog.line_ids.operation, 'create')
        self.assertFalse(self.env['product.supplierinfo'].search([('partner_id', '=', self.acme.id)]))
        dialog.with_user(self.bob).action_confirm()
        info = self.env['product.supplierinfo'].search([('partner_id', '=', self.acme.id)])
        self.assertEqual(len(info), 1)
        self.assertEqual(info.price, 11.5)
        self.assertEqual(info.spm_contract_line_id, contract.line_ids)

    def test_sc_0067_2_activation_and_revision_leave_vendor_prices_alone(self):
        """Requirements: FR-SPM-0067. SC-SPM-0067-2: activating and revising a contract
        changes no vendor price line until Update vendor prices is confirmed."""
        info = self._supplierinfo(12.0)
        contract = self._active_contract(lines=[self._line_vals(price=11.5)])
        self.assertEqual(info.price, 12.0)
        contract.with_user(self.ann).action_spm_revise()
        contract.line_ids.with_user(self.ann).write({'price_unit': 11.0})
        contract.with_user(self.ann).action_spm_submit()
        with freeze_time('2027-06-01 09:00:00'):
            contract.with_user(self.bob).action_spm_approve()
        self.assertEqual(contract.state, 'active')
        info.invalidate_recordset()
        self.assertEqual(info.price, 12.0)
        self.assertEqual(self.env['product.supplierinfo'].search_count(
            [('partner_id', '=', self.acme.id)]), 1)

    # BR-SPM-0011, FR-SPM-0068 ----------------------------------------------
    def test_sc_0068_1_consumption_net_of_cancellations_and_returns(self):
        """Requirements: BR-SPM-0011, FR-SPM-0068. SC-SPM-0068-1: ceiling 1,000 chairs; confirmed
        orders of 300 and 200 chairs, 20 of the 200 cancelled (the line is reduced to 180),
        10 chairs returned. Consumed 470, remaining 530, used 47 percent."""
        contract = self._active_contract(lines=[self._line_vals(price=11.5, ceiling=1000.0)])
        order1 = self._order(self.acme, 300.0, 11.5, confirm=True)
        order2 = self._order(self.acme, 200.0, 11.5, confirm=True)
        self.assertEqual(order1.order_line.spm_contract_line_id, contract.line_ids)
        self.assertEqual(order2.order_line.spm_contract_line_id, contract.line_ids)
        order2.order_line.product_qty = 180.0
        picking = self._receive(order1, 300.0)
        self._return(picking, 10.0)
        self.env.flush_all()
        contract.invalidate_recordset()
        line = contract.line_ids
        line.invalidate_recordset()
        self.assertEqual(line.consumed_qty, 470.0)
        self.assertEqual(line.remaining_qty, 530.0)
        self.assertIn(round(line.used_share, 2), (0.47, 47.0), "47 percent, as a fraction or a percentage")
        self.assertAlmostEqual(line.consumed_value, 470.0 * 11.5, places=2)
        self.assertAlmostEqual(contract.consumed_value, 470.0 * 11.5, places=2)

    def test_br_0011_cancelled_order_is_not_consumed(self):
        """Requirements: BR-SPM-0011: a cancelled order consumes nothing."""
        contract = self._active_contract(lines=[self._line_vals()])
        order = self._order(self.acme, 100.0, 11.5, confirm=True)
        self.assertEqual(contract.line_ids.consumed_qty, 100.0)
        order.button_cancel()
        self.env.flush_all()
        contract.line_ids.invalidate_recordset()
        self.assertEqual(contract.line_ids.consumed_qty, 0.0)

    # FR-SPM-0069 -----------------------------------------------------------
    def test_sc_0069_1_price_and_quantity_deviation_warn_without_blocking(self):
        """Requirements: FR-SPM-0069. SC-SPM-0069-1: contract 11.50, minimum 20; 10 chairs at 12.00."""
        self._active_contract(lines=[self._line_vals(price=11.5, min_qty=20.0)])
        order = self._order(self.acme, 10.0, 12.0)
        self.assertTrue(order.exists(), "the order is saved")
        warning = (order.order_line.spm_contract_warning or '').lower()
        self.assertIn('above', warning)
        self.assertIn('contract price', warning)
        self.assertIn('minimum', warning)
        self.assertTrue(order.spm_contract_warning, "the warnings are listed on the order")

    def test_fr_0069_no_warning_at_the_contract_terms(self):
        """Requirements: FR-SPM-0069. Boundary: price equal to the contract price and quantity
        equal to the minimum give no warning."""
        self._active_contract(lines=[self._line_vals(price=11.5, min_qty=20.0)])
        order = self._order(self.acme, 20.0, 11.5)
        self.assertFalse(order.order_line.spm_contract_warning)
        self.assertFalse(order.spm_contract_warning)

    def test_fr_0069_expected_date_before_lead_time_and_ceiling_warn(self):
        """Requirements: FR-SPM-0069: an expected date earlier than the 10-day lead time allows,
        and a quantity that would exceed the ceiling, each warn."""
        self._active_contract(lines=[self._line_vals(price=11.5, min_qty=1.0, lead=10, ceiling=100.0)])
        soon = (date.today() + timedelta(days=3)).isoformat() + ' 08:00:00'
        order = self._order(self.acme, 150.0, 11.5, planned=soon)
        warning = (order.order_line.spm_contract_warning or '').lower()
        self.assertIn('lead', warning)
        self.assertIn('ceiling', warning)

    def test_sc_0069_2_product_under_another_suppliers_contract_warns(self):
        """Requirements: FR-SPM-0069. SC-SPM-0069-2: chairs are under contract with Acme;
        a Buyer orders them from Beta."""
        self._active_contract(partner=self.acme)
        order = self._order(self.beta, 30.0, 11.5)
        warning = order.order_line.spm_contract_warning or ''
        self.assertIn('Acme', warning)

    # FR-SPM-0070 -----------------------------------------------------------
    def test_sc_0070_1_filter_on_notice_date_shows_days_and_share(self):
        """Requirements: FR-SPM-0070. SC-SPM-0070-1: three active contracts with notice dates in
        10, 40 and 200 days; filtering on notice date within 60 days lists two, with their
        days to notice and used share."""
        today = date(2027, 1, 1)
        contracts = self.env['procurement.contract']
        for days in (10, 40, 200):
            contracts |= self._active_contract(
                start='2026-06-01', end=(today + timedelta(days=days + 30)).isoformat(),
                notice=30, lines=[self._line_vals()])
        with freeze_time('2027-01-01 08:00:00'):
            found = self.env['procurement.contract'].search([
                ('id', 'in', contracts.ids), ('state', '=', 'active'),
                ('notice_date', '>=', today), ('notice_date', '<=', today + timedelta(days=60)),
            ])
            self.assertEqual(len(found), 2)
            found.invalidate_recordset()
            self.assertEqual(sorted(found.mapped('days_to_notice')), [10, 40])
            self.assertEqual(found.mapped('used_share'), [0.0, 0.0])
