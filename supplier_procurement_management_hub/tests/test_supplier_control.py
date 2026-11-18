"""Unit tests of supplier control (T-009).

Requirements: FR-SPM-0013, BR-SPM-0006
The override dialog is opened with Form; the Override button that opens it is left to the screen review.
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form
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

    def test_override_takes_supplier_from_the_record(self):
        """FR-SPM-0013: the supplier of an override is the order's, whatever the client sends."""
        order = self._order('blocked')
        other = self.env['res.partner'].create({'name': 'Delta T9'})
        override = self._override(order, partner_id=other.id)
        self.assertEqual(override.partner_id, self.supplier)

    def test_override_applies_to_its_record_and_action_only(self):
        """FR-SPM-0013: an override of one order and action covers no other order or action."""
        order = self._order('blocked')
        other_order = self._order('blocked')
        self._override(order, action='invite')
        with self.assertRaises(UserError):
            order.with_user(self.buyer).button_confirm()
        self._override(order)
        with self.assertRaises(UserError):
            other_order.with_user(self.buyer).button_confirm()
        order.with_user(self.buyer).button_confirm()
        self.assertEqual(order.state, 'purchase')

    def test_supplier_without_status_is_refused_by_name(self):
        """BR-SPM-0006: a supplier with no status is not allowed."""
        order = self.env['purchase.order'].create({
            'partner_id': self.supplier.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 1, 'price_unit': 1})],
        })
        self.supplier.spm_status_id = False
        with self.assertRaises(UserError) as caught:
            order.with_user(self.buyer).button_confirm()
        self.assertIn(self.supplier.name, str(caught.exception))

    def test_approve_order_is_guarded_after_the_status_changed(self):
        """FR-SPM-0013: Approve Order of a waiting order refuses a supplier suspended since."""
        order = self._order('approved')
        self.company.po_double_validation = 'two_step'
        self.company.po_double_validation_amount = 0
        order.with_user(self.buyer).button_confirm()
        self.assertEqual(order.state, 'to approve')
        self.supplier.with_user(self.manager).action_spm_set_status(self.statuses['suspended'], reason='x')
        with self.assertRaises(UserError):
            order.with_user(self.manager).button_approve()
        self.assertEqual(order.state, 'to approve')

    def test_direct_approve_of_a_draft_order_is_guarded(self):
        """FR-SPM-0013: button_approve called on a draft order does not skip the check."""
        order = self._order('blocked')
        with self.assertRaises(UserError):
            order.with_user(self.manager).button_approve()
        self.assertEqual(order.state, 'draft')

    def test_override_for_another_company_or_missing_record_is_refused(self):
        """BR-SPM-0001: a manager cannot override a record they cannot read or that does not exist."""
        other_company = self.env['res.company'].create({'name': 'Other T9'})
        order = self._order('blocked')
        order.company_id = other_company
        with self.assertRaises(AccessError):
            self._override(order)
        with self.assertRaises(UserError):
            self._override(order, res_id=order.id + 100000)
        with self.assertRaises(UserError):
            self._override(order, res_model='res.partner')

    def test_override_company_comes_from_the_record(self):
        """BR-SPM-0001: the company of an override is the order's."""
        order = self._order('blocked')
        other_company = self.env['res.company'].create({'name': 'Other2 T9'})
        override = self._override(order, company_id=other_company.id)
        self.assertEqual(override.company_id, order.company_id)

    def test_blank_reason_is_refused_and_date_is_forced(self):
        """FR-SPM-0013: an override needs a reason and cannot be backdated."""
        order = self._order('blocked')
        with self.assertRaises(UserError):
            self._override(order, reason='   ')
        override = self._override(order, date='2000-01-01 00:00:00')
        self.assertGreater(override.date.year, 2000)

    def test_override_cannot_be_changed(self):
        """FR-SPM-0013: nobody writes an override."""
        override = self._override(self._order('blocked'))
        with self.assertRaises(AccessError):
            override.with_user(self.manager).write({'reason': 'Other'})

    def test_override_dialog_records_the_override(self):
        """FR-SPM-0013: the manager fills the dialog; the buyer then confirms. The button is not pressed here."""
        order = self._order('blocked')
        action = order.with_user(self.manager).action_spm_open_override_wizard()
        with Form(self.env['procurement.reason.wizard'].with_user(self.manager).with_context(
                **action['context'])) as form:
            form.reason = 'Emergency'
        form.record.action_confirm()
        order.with_user(self.buyer).button_confirm()
        self.assertEqual(order.state, 'purchase')

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
