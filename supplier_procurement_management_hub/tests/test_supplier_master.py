"""Own tests of T-003 (supplier master)."""
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestSupplierMaster(TransactionCase):
    """Requirements: FR-SPM-0011, FR-SPM-0014, FR-SPM-0108, BR-SPM-0004, BR-SPM-0005"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        partner_manager = self.env.ref('base.group_partner_manager')
        self.manager = self.env['res.users'].create({
            'name': 'Mia', 'login': 'spm_t003_mgr', 'email': 'mgr@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_manager').id, partner_manager.id])]})
        self.buyer = self.env['res.users'].create({
            'name': 'Bea', 'login': 'spm_t003_buyer', 'email': 'buyer@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_buyer').id, partner_manager.id])]})
        self.acme = self.env['res.partner'].create({'name': 'Acme', 'is_company': True})
        self.blocked = self.env['procurement.supplier.status'].search([('kind', '=', 'blocked')], limit=1)

    def test_status_written_directly_by_a_buyer_is_refused(self):
        """FR-SPM-0011: only the status action writes the status."""
        with self.assertRaises(AccessError):
            self.acme.with_user(self.buyer).write({'spm_status_id': self.blocked.id})

    def test_status_dialog_changes_the_status_with_its_reason(self):
        """FR-SPM-0011: the dialog (Form sets fields and saves; the button press
        is left to the screen review) carries the new status and the reason."""
        wizard_model = self.env['procurement.reason.wizard'].with_user(self.manager).with_context(
            default_res_model='res.partner', default_res_ids=self.acme.ids, default_action='set_status')
        form = Form(wizard_model)
        form.status_id = self.blocked
        form.reason = 'Fraud suspected'
        wizard = form.save()
        wizard.action_confirm()
        self.assertEqual(self.acme.spm_status_id, self.blocked)
        self.assertEqual(self.acme.spm_change_ids.reason, 'Fraud suspected')

    def test_vat_change_keeps_the_value_in_restricted_fields(self):
        """BR-SPM-0004: a tax identifier change is kept in the restricted values only."""
        self.acme.vat = 'BE0123456749'
        change = self.acme.spm_change_ids
        self.assertFalse(change.old_value or change.new_value)
        self.assertEqual(change.restricted_new_value, 'BE0123456749')

    def test_bank_account_change_is_in_the_history(self):
        """FR-SPM-0014: a bank account added and removed writes two rows."""
        bank = self.env['res.partner.bank'].create({'partner_id': self.acme.id, 'account_number': 'BE68539007547034'})
        bank.unlink()
        changes = self.acme.spm_change_ids.filtered(lambda c: c.field_name == 'bank_ids')
        self.assertEqual(sorted(changes.mapped('restricted_new_value')), ['', 'BE68 5390 0754 7034'])

    def test_portal_field_refuses_bank_status_and_risk_fields(self):
        """BR-SPM-0004: a portal field is never a status, risk or bank field."""
        field = self.env['ir.model.fields']._get('res.partner', 'spm_status_id')
        with self.assertRaises(ValidationError):
            self.env['procurement.portal.field'].create({'field_id': field.id})
        phone = self.env['ir.model.fields']._get('res.partner', 'phone')
        self.assertTrue(self.env['procurement.portal.field'].create({'field_id': phone.id}))

    def test_document_type_reminder_is_not_negative(self):
        """FR-SPM-0108: a negative reminder period is refused; zero is accepted."""
        DocType = self.env['procurement.document.type']
        self.assertTrue(DocType.create({'name': 'X', 'reminder_days': 0}))
        with self.assertRaises(ValidationError):
            DocType.create({'name': 'Y', 'reminder_days': -1})
