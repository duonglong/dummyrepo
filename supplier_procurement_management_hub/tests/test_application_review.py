from odoo.exceptions import AccessError, UserError
from odoo.tests import Form
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestApplicationReview(TransactionCase):
    """Requirements: FR-SPM-0004, FR-SPM-0005, FR-SPM-0006"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        partner_manager = self.env.ref('base.group_partner_manager').id
        self.manager = self.env['res.users'].create({
            'name': 'Mia Manager', 'login': 'spm_manager_t006u', 'email': 'spm_manager_t006u@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_manager').id, partner_manager])],
        })
        self.goods = self.env['procurement.supplier.type'].create({'name': 'Goods T006u'})
        self.application = self.env['procurement.supplier.application'].with_user(self.manager).create({
            'email': 'sales@acme.example', 'legal_name': 'Acme Ltd', 'vat': 'BE0123456789',
            'supplier_type_id': self.goods.id, 'city': 'Ghent', 'bank_account_number': 'BE68539007547034',
            'bank_name': 'Bank', 'contact_ids': [(0, 0, {'name': 'Sam Sales', 'email': 's@acme.example'})],
        })
        self.application.with_user(self.manager).action_spm_submit()

    def test_approval_copies_data_bank_and_people(self):
        """FR-SPM-0005: the contact gets the application's data, its bank account and its people."""
        self.application.with_user(self.manager).action_spm_approve()
        partner = self.application.partner_id
        self.assertEqual(partner.city, 'Ghent')
        self.assertEqual(partner.spm_supplier_type_id, self.goods)
        self.assertEqual(partner.bank_ids.sanitized_account_number, 'BE68539007547034')
        self.assertEqual(partner.child_ids.mapped('name'), ['Sam Sales'])

    def test_a_closed_application_cannot_be_reviewed_again(self):
        """FR-SPM-0005, FR-SPM-0006: only a submitted application is reviewed."""
        self.application.with_user(self.manager).action_spm_reject('No')
        for action in (lambda a: a.action_spm_approve(), lambda a: a.action_spm_reject('Again'),
                       lambda a: a.action_spm_request_changes('Again')):
            with self.assertRaises(UserError):
                action(self.application.with_user(self.manager))

    def test_a_user_without_the_buyer_role_cannot_request_changes(self):
        """FR-SPM-0004: the right to write the application is checked."""
        outsider = self.env['res.users'].create({
            'name': 'Out', 'login': 'spm_out_t006u', 'email': 'out_t006u@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.application.with_user(outsider).action_spm_request_changes('Please fix')

    def test_reason_dialog_rejects_the_application(self):
        """FR-SPM-0006: the reason dialog opened as the screen does forwards the reason.

        Form fills and saves the dialog; the Reject button that opens it stays for the screen review.
        """
        context = self.application.with_user(self.manager).action_spm_open_reject_wizard()['context']
        form = Form(self.env['procurement.reason.wizard'].with_user(self.manager).with_context(**context))
        form.reason = 'No ISO certificate'
        form.save().action_confirm()
        self.assertEqual(self.application.state, 'rejected')
        self.assertEqual(self.application.rejection_reason, 'No ISO certificate')

    def test_approval_copies_operating_locations(self):
        """FR-SPM-0005: the operating locations of the application go to the contact."""
        self.application.sudo().write({'operating_locations': 'Ghent, Antwerp'})
        self.application.with_user(self.manager).action_spm_approve()
        self.assertEqual(self.application.partner_id.spm_operating_locations, 'Ghent, Antwerp')

    def test_approval_without_a_default_approved_status_is_refused(self):
        """FR-SPM-0005: no approved status configured leaves the application submitted."""
        self.env['procurement.supplier.status'].sudo().search(
            [('kind', '=', 'approved')]).write({'default_for_kind': False})
        with self.assertRaises(UserError):
            self.application.with_user(self.manager).action_spm_approve()
        self.assertEqual(self.application.state, 'submitted')

    def test_approval_changes_carry_the_approval_origin(self):
        """FR-SPM-0005: the history rows of an existing contact say approval, not back office."""
        partner = self.env['res.partner'].create({
            'name': 'Acme Old', 'is_company': True, 'vat': ' be0123456789 '})
        self.application.with_user(self.manager).action_spm_approve()
        self.assertEqual(self.application.partner_id, partner)
        changes = self.env['procurement.supplier.change'].sudo().search([('partner_id', '=', partner.id)])
        self.assertTrue(changes)
        self.assertEqual(set(changes.mapped('origin')), {'approval'})
        self.assertTrue(partner.spm_status_id)
        self.assertEqual(partner.name, 'Acme Old')
        self.assertEqual(partner.city, 'Ghent')

    def _spm_block_matching_supplier(self, kind):
        """Return a contact with the tax id of the application, set to a status of ``kind`` through the manager action."""
        partner = self.env['res.partner'].create({'name': 'Acme Old', 'is_company': True, 'vat': 'BE0123456789'})
        status = self.env['procurement.supplier.status'].create({'name': f'{kind} T0257', 'kind': kind})
        partner.with_user(self.manager).action_spm_set_status(status, 'Audit failed')
        return partner, status

    def test_bug_b_0257_approval_refuses_a_blocked_match(self):
        """Regression test for B-0257: a new application for the tax id of a Blocked supplier is refused.

        Requirements: FR-SPM-0005, FR-SPM-0011, BR-SPM-0006
        """
        partner, status = self._spm_block_matching_supplier('blocked')
        with self.assertRaises(UserError) as caught:
            self.application.with_user(self.manager).action_spm_approve()
        self.assertIn('Acme Old', str(caught.exception))
        self.assertEqual(partner.spm_status_id, status)
        self.assertEqual(self.application.state, 'submitted')

    def test_bug_b_0257_approval_refuses_a_suspended_match(self):
        """Regression test for B-0257: the Suspended kind is refused the same way.

        Requirements: FR-SPM-0005, FR-SPM-0011, BR-SPM-0006
        """
        partner, status = self._spm_block_matching_supplier('suspended')
        with self.assertRaises(UserError):
            self.application.with_user(self.manager).action_spm_approve()
        self.assertEqual(partner.spm_status_id, status)

    def test_bug_b_0257_requalification_approval_refuses_a_blocked_supplier(self):
        """Regression test for B-0257: a re-qualification application of a Blocked supplier is refused.

        Requirements: FR-SPM-0005, FR-SPM-0011, BR-SPM-0006
        """
        partner, status = self._spm_block_matching_supplier('blocked')
        self.application.sudo().write({'partner_id': partner.id, 'is_requalification': True})
        with self.assertRaises(UserError):
            self.application.with_user(self.manager).action_spm_approve()
        self.assertEqual(partner.spm_status_id, status)
