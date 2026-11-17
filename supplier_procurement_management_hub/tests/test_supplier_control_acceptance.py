"""Acceptance tests of T-009 (supplier control), written from the specification.

Requirements: FR-SPM-0013, BR-SPM-0006

The override is recorded by creating a ``procurement.override`` row as a
Procurement manager (fields from the design); the reason dialog that wraps it
in the UI (``procurement.reason.wizard``, action 'override') names no method
for it, so that form step is left to the build.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestSupplierControlAcceptance(TransactionCase):
    """Requirements: FR-SPM-0013, BR-SPM-0006"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.company_a = self.env.company
        self.company_b = self.env['res.company'].create({'name': 'SPM Branch B'})
        companies = [self.company_a.id, self.company_b.id]
        self.buyer = self.env['res.users'].create({
            'name': 'Bea Buyer',
            'login': 'spm_buyer_t009',
            'email': 'spm_buyer_t009@example.com',
            'company_id': self.company_a.id,
            'company_ids': [(6, 0, companies)],
            'group_ids': [(6, 0, [
                self.env.ref(f'{MODULE}.group_spm_buyer').id,
                self.env.ref('purchase.group_purchase_user').id,
            ])],
        })
        self.manager = self.env['res.users'].create({
            'name': 'Pam Manager',
            'login': 'spm_manager_t009',
            'email': 'spm_manager_t009@example.com',
            'company_id': self.company_a.id,
            'company_ids': [(6, 0, companies)],
            'group_ids': [(6, 0, [
                self.env.ref(f'{MODULE}.group_spm_manager').id,
                self.env.ref('purchase.group_purchase_manager').id,
            ])],
        })
        prospect = self.env['procurement.supplier.status'].search(
            [('kind', '=', 'prospect')], limit=1)
        self.prospect_name = prospect.name
        self.beta = self.env['res.partner'].create({'name': 'Beta', 'is_company': True})
        self.beta.with_user(self.manager).write({'spm_status_id': prospect.id})
        self.product = self.env['product.product'].create({
            'name': 'SPM test part', 'type': 'consu', 'standard_price': 5.0})

    def _order(self, company):
        return self.env['purchase.order'].with_company(company).create({
            'partner_id': self.beta.id,
            'company_id': company.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 2.0,
                'price_unit': 5.0,
            })],
        })

    def _confirm_as_buyer(self, order):
        order.with_user(self.buyer).with_company(order.company_id).button_confirm()

    def _override(self, order):
        return self.env['procurement.override'].with_user(self.manager).create({
            'res_model': 'purchase.order',
            'res_id': order.id,
            'partner_id': self.beta.id,
            'action': 'confirm',
            'kind': 'status',
            'reason': 'Emergency repair',
            'company_id': order.company_id.id,
        })

    def test_sc_0013_1_prospect_supplier_refused_when_control_enabled(self):
        """SC-SPM-0013-1: company A has supplier control; Beta is a Prospect;
        the Buyer's confirmation is refused with a message naming Beta and Prospect."""
        self.company_a.spm_supplier_control = True
        order = self._order(self.company_a)
        with self.assertRaises(UserError) as caught:
            self._confirm_as_buyer(order)
        message = str(caught.exception)
        self.assertIn('Beta', message)
        self.assertIn(self.prospect_name, message)
        self.assertEqual(order.state, 'draft')

    def test_sc_0013_2_override_with_reason_lets_the_buyer_confirm_and_is_audited(self):
        """SC-SPM-0013-2: a Procurement manager records an override 'Emergency
        repair' for the order; the Buyer confirms; an audit entry holds the
        override and its reason. The reason dialog step is left to the build."""
        self.company_a.spm_supplier_control = True
        order = self._order(self.company_a)
        with self.assertRaises(UserError):
            self._confirm_as_buyer(order)
        override = self._override(order)
        self.assertEqual(override.user_id, self.manager)
        self._confirm_as_buyer(order)
        self.assertEqual(order.state, 'purchase')
        self.env.flush_all()
        selection = [k for k, _l in self.env['procurement.audit.log']._fields['event'].selection]
        keys = [k for k in selection if 'override' in k]
        self.assertTrue(keys, "the audit event list has an override kind")
        entries = self.env['procurement.audit.log'].search([('event', 'in', keys)])
        self.assertTrue(
            any('Emergency repair' in ' '.join(
                [e.comment or '', str(e.new_values or ''), str(e.old_values or '')])
                for e in entries),
            "an audit entry holds the override reason")

    def test_override_is_scoped_to_its_one_record(self):
        """FR-SPM-0013 (override limited to one record): an override for one
        order does not let the Buyer confirm another order with Beta."""
        self.company_a.spm_supplier_control = True
        order = self._order(self.company_a)
        other = self._order(self.company_a)
        self._override(order)
        self._confirm_as_buyer(order)
        with self.assertRaises(UserError):
            self._confirm_as_buyer(other)

    def test_sc_0013_3_control_disabled_confirms_normally(self):
        """SC-SPM-0013-3: company B has supplier control disabled; the Buyer
        confirms an order of company B with Beta and it is confirmed."""
        self.company_a.spm_supplier_control = True
        self.company_b.spm_supplier_control = False
        order = self._order(self.company_b)
        self._confirm_as_buyer(order)
        self.assertEqual(order.state, 'purchase')
