"""Unit tests of supplier control (T-009).

Requirements: FR-SPM-0013, BR-SPM-0006
The reason dialog that wraps an override is a screen step and is not tested here.
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestSupplierControl(TransactionCase):
    """Requirements: FR-SPM-0013, BR-SPM-0006"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.company = self.env.company
        self.company.spm_supplier_control = True
        self.buyer = self._user('buyer', f'{MODULE}.group_spm_buyer', 'purchase.group_purchase_user')
        self.manager = self._user('manager', f'{MODULE}.group_spm_manager',
                                  'purchase.group_purchase_manager')
        self.statuses = {s.kind: s for s in self.env['procurement.supplier.status'].search([])}
        self.supplier = self.env['res.partner'].create({'name': 'Gamma T9', 'is_company': True})
        self.product = self.env['product.product'].create({'name': 'T9 part', 'type': 'consu'})

    def _user(self, name, *group_refs):
        return self.env['res.users'].create({
            'name': f'{name} T9', 'login': f'spm_t9_{name}', 'email': f'{name}9@example.com',
            'group_ids': [(6, 0, [self.env.ref(ref).id for ref in group_refs])]})

    def _order(self, kind):
        self.supplier.with_user(self.manager).action_spm_set_status(self.statuses[kind], reason='Test')
        return self.env['purchase.order'].create({
            'partner_id': self.supplier.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 1, 'price_unit': 1})],
        })

    def _override_values(self, order, **vals):
        values = {'res_model': 'purchase.order', 'res_id': order.id,
                  'partner_id': self.supplier.id, 'action': 'confirm', 'kind': 'status',
                  'reason': 'Urgent', 'company_id': order.company_id.id}
        values.update(vals)
        return values

    def _override(self, order, **vals):
        return self.env['procurement.override'].with_user(self.manager).create(
            self._override_values(order, **vals))

    def test_approved_and_conditional_are_allowed(self):
        """BR-SPM-0006: Approved and Conditional suppliers confirm."""
        for kind in ('approved', 'conditional'):
            order = self._order(kind)
            order.with_user(self.buyer).button_confirm()
            self.assertEqual(order.state, 'purchase')

    def test_other_kinds_are_refused_naming_the_status(self):
        """BR-SPM-0006: every other kind is refused with the status name."""
        for kind in ('in_review', 'requalification_due', 'suspended', 'blocked'):
            order = self._order(kind)
            with self.assertRaises(UserError) as caught:
                order.with_user(self.buyer).button_confirm()
            self.assertIn(self.statuses[kind].name, str(caught.exception))

    def test_override_of_another_supplier_does_not_apply(self):
        """FR-SPM-0013: an override names the supplier it was recorded for."""
        order = self._order('blocked')
        other = self.env['res.partner'].create({'name': 'Delta T9'})
        self._override(order, partner_id=other.id)
        with self.assertRaises(UserError):
            order.with_user(self.buyer).button_confirm()

    def test_buyer_cannot_record_override(self):
        """FR-SPM-0013: only a Procurement manager records an override."""
        order = self._order('blocked')
        with self.assertRaises(AccessError):
            self.env['procurement.override'].with_user(self.buyer).create(
                self._override_values(order))

    def test_override_user_is_the_acting_manager_and_is_audited(self):
        """FR-SPM-0013: the override holds its author and writes an audit entry."""
        order = self._order('blocked')
        override = self._override(order, user_id=self.buyer.id)
        self.assertEqual(override.user_id, self.manager)
        entry = self.env['procurement.audit.log'].search(
            [('event', '=', 'status_override'), ('res_id', '=', override.id)])
        self.assertEqual(entry.comment, 'Urgent')
        self.assertEqual(entry.user_id, self.manager)

    def test_override_cannot_be_deleted(self):
        """FR-SPM-0013: a manager cannot delete an override."""
        override = self._override(self._order('blocked'))
        with self.assertRaises(AccessError):
            override.with_user(self.manager).unlink()
