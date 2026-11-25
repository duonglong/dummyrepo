"""Acceptance tests of T-003 (supplier lifecycle status), written from the specification.

Requirements: FR-SPM-0011, BR-SPM-0005

The reason dialog (procurement.reason.wizard) carries only a reason, not the
new status, so the status change is tested on the method
res.partner.action_spm_set_status(status, reason=None) that the design names;
what the dialog adds is left to the build.
"""
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestSupplierStatusAcceptance(TransactionCase):
    """Requirements: FR-SPM-0011"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.manager = self.env['res.users'].create({
            'name': 'Mia Manager', 'login': 'spm_manager_t003',
            'email': 'spm_manager_t003@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_manager').id,
                                  self.env.ref('base.group_partner_manager').id])],
        })
        Status = self.env['procurement.supplier.status']
        self.approved = Status.search([('kind', '=', 'approved')], limit=1)
        self.suspended = Status.search([('kind', '=', 'suspended')], limit=1)
        self.blocked = Status.search([('kind', '=', 'blocked')], limit=1)
        self.acme = self.env['res.partner'].create({
            'name': 'Acme', 'is_company': True, 'spm_status_id': self.approved.id})

    def _history(self):
        return self.acme.spm_change_ids.filtered(lambda c: c.field_name == 'spm_status_id')

    def test_sc_0011_1_suspension_is_in_the_status_history(self):
        """SC-SPM-0011-1: a Procurement manager sets Acme to Suspended with a
        reason; the history shows Approved to Suspended with the reason, user
        and date, and the audit log holds the change."""
        self.acme.with_user(self.manager).action_spm_set_status(
            self.suspended, reason='Repeated late deliveries')
        self.env.flush_all()
        self.acme.invalidate_recordset()
        self.assertEqual(self.acme.spm_status_kind, 'suspended')
        history = self._history()
        self.assertEqual(len(history), 1)
        self.assertIn(self.approved.name, history.old_value)
        self.assertIn(self.suspended.name, history.new_value)
        self.assertEqual(history.reason, 'Repeated late deliveries')
        self.assertEqual(history.user_id, self.manager)
        self.assertTrue(history.date)
        audit = self.env['procurement.audit.log'].search([
            ('res_model', '=', 'res.partner'), ('res_id', '=', self.acme.id)])
        self.assertIn('Repeated late deliveries', ' '.join(audit.mapped(lambda e: e.comment or '')))

    def test_sc_0011_2_blocking_without_a_reason_is_refused(self):
        """SC-SPM-0011-2: setting Blocked without a reason is refused with a
        message that a reason is required; the status stays."""
        with self.assertRaises((UserError, ValidationError)) as ctx:
            self.acme.with_user(self.manager).action_spm_set_status(self.blocked)
        self.assertIn('reason', str(ctx.exception).lower())
        self.assertEqual(self.acme.spm_status_id, self.approved)

    def test_suspension_with_a_blank_reason_is_refused(self):
        """FR-SPM-0011 boundary: a blank reason counts as none for a Suspended kind."""
        with self.assertRaises((UserError, ValidationError)):
            self.acme.with_user(self.manager).action_spm_set_status(self.suspended, reason='   ')

    def test_a_kind_other_than_suspended_or_blocked_needs_no_reason(self):
        """FR-SPM-0011 boundary: the reason is required only for kinds Suspended and Blocked."""
        in_review = self.env['procurement.supplier.status'].search([('kind', '=', 'in_review')], limit=1)
        self.acme.with_user(self.manager).action_spm_set_status(in_review)
        self.assertEqual(self.acme.spm_status_id, in_review)

    def test_a_buyer_cannot_change_the_status(self):
        """FR-SPM-0011 / design lifecycle: only a Procurement manager changes the status."""
        buyer = self.env['res.users'].create({
            'name': 'Bea Buyer', 'login': 'spm_buyer_t003b', 'email': 'spm_buyer_t003b@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_buyer').id,
                                  self.env.ref('base.group_partner_manager').id])],
        })
        with self.assertRaises(AccessError):
            self.acme.with_user(buyer).action_spm_set_status(self.suspended, reason='x')
