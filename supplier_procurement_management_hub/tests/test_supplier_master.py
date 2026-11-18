"""Own tests of T-003 (supplier master)."""
from odoo.exceptions import AccessError, UserError, ValidationError
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

    # --- fix round: guard, rights, history scope ---------------------------

    def _manager_without_contact_rights(self):
        return self.env['res.users'].create({
            'name': 'Max', 'login': 'spm_t003_only_mgr', 'email': 'max@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_manager').id])]})

    def test_context_flag_does_not_open_the_status_guard(self):
        """FR-SPM-0011: a context key sent by the client never lets a status be written."""
        for user in (self.buyer, self.manager):
            with self.assertRaises(AccessError):
                self.acme.with_user(user).with_context(
                    spm_status_allowed=True, spm_status_reason='x').write({'spm_status_id': self.blocked.id})
        self.assertFalse(self.acme.spm_status_id)

    def test_manager_with_only_the_hub_role_changes_the_status(self):
        """FR-SPM-0011: the role alone is enough; the history keeps the manager as user."""
        manager = self._manager_without_contact_rights()
        suspended = self.env['procurement.supplier.status'].search([('kind', '=', 'suspended')], limit=1)
        self.acme.with_user(manager).action_spm_set_status(suspended, 'Late deliveries')
        change = self.acme.spm_change_ids.filtered(lambda c: c.field_name == 'spm_status_id')
        self.assertEqual(self.acme.spm_status_id, suspended)
        self.assertEqual((change.user_id, change.reason), (manager, 'Late deliveries'))

    def test_reason_is_still_required_for_a_blocked_status(self):
        """FR-SPM-0011: no reason, no block, whatever the context says."""
        manager = self._manager_without_contact_rights()
        with self.assertRaises(UserError):
            self.acme.with_user(manager).with_context(spm_status_reason='sneaky').action_spm_set_status(
                self.blocked, '')

    def test_origin_named_by_a_user_context_is_ignored(self):
        """FR-SPM-0014: a back-office user cannot record the origin portal."""
        self.acme.with_user(self.buyer).with_context(spm_change_origin='portal').write({'street': 'Oak 1'})
        self.assertEqual(self.acme.spm_change_ids.origin, 'backoffice')
        self.acme.sudo().with_context(spm_change_origin='portal').write({'street': 'Oak 2'})
        self.assertIn('portal', self.acme.spm_change_ids.mapped('origin'))

    def test_watched_fields_write_one_row_each(self):
        """FR-SPM-0014: legal name, categories, risk level and re-qualification date are logged."""
        category = self.env['product.category'].create({'name': 'Steel'})
        self.acme.write({
            'name': 'Acme Ltd', 'spm_product_category_ids': [(4, category.id)],
            'spm_risk_level': 'high', 'spm_requalification_date': '2027-01-31'})
        names = set(self.acme.spm_change_ids.mapped('field_name'))
        self.assertEqual(
            names, {'name', 'spm_product_category_ids', 'spm_risk_level', 'spm_requalification_date'})
        risk = self.acme.spm_change_ids.filtered(lambda c: c.field_name == 'spm_risk_level')
        self.assertEqual(risk.restricted_new_value, 'High')

    def test_employee_bank_account_is_not_logged(self):
        """BR-SPM-0004: an employee's bank account writes no change row and no audit entry."""
        employee = self.env['hr.employee'].create({'name': 'Eve'})
        contact = employee.work_contact_id
        audit_before = self.env['procurement.audit.log'].search_count([])
        bank = self.env['res.partner.bank'].create({'partner_id': contact.id, 'account_number': 'BE68539007547034'})
        bank.write({'holder_name': 'Eve E'})
        bank.unlink()
        self.assertFalse(self.env['procurement.supplier.change'].search([('partner_id', '=', contact.id)]))
        self.assertEqual(self.env['procurement.audit.log'].search_count([]), audit_before)

    def test_customer_address_change_is_not_logged(self):
        """FR-SPM-0014: a customer's address change writes no history."""
        customer = self.env['res.partner'].create({'name': 'Cust', 'is_company': True, 'customer_rank': 1})
        customer.write({'street': 'Elm 3', 'vat': 'BE0123456749'})
        self.assertFalse(customer.spm_change_ids)

    def test_bank_holder_change_and_move_are_logged(self):
        """FR-SPM-0014: a holder change and a move to another contact are in the history."""
        other = self.env['res.partner'].create({'name': 'Other', 'is_company': True})
        bank = self.env['res.partner.bank'].create({'partner_id': self.acme.id, 'account_number': 'BE68539007547034'})
        bank.write({'holder_name': 'Acme Holder'})
        self.assertTrue(any('Acme Holder' in value for value in self.acme.spm_change_ids.mapped('restricted_new_value')))
        bank.write({'partner_id': other.id})
        self.assertEqual(len(other.spm_change_ids), 1)
        moved_away = self.acme.spm_change_ids.filtered(
            lambda change: change.field_name == 'bank_ids' and not change.restricted_new_value)
        self.assertIn('BE68 5390 0754 7034', moved_away.restricted_old_value)

    def test_portal_field_allows_only_open_plain_fields(self):
        """BR-SPM-0004: a field with groups, or a computed one marked editable, is refused."""
        PortalField = self.env['procurement.portal.field']
        restricted = self.env['ir.model.fields']._get('res.partner', 'credit_limit')
        with self.assertRaises(ValidationError):
            PortalField.create({'field_id': restricted.id})
        computed = self.env['ir.model.fields']._get('res.partner', 'display_name')
        with self.assertRaises(ValidationError):
            PortalField.create({'field_id': computed.id, 'editable': True})

    def test_reason_dialog_refuses_a_model_other_than_suppliers(self):
        """FR-SPM-0011: the dialog never runs the action on another model."""
        wizard = self.env['procurement.reason.wizard'].with_user(self.manager).create({
            'res_model': 'res.users', 'res_ids': [1], 'action': 'set_status', 'status_id': self.blocked.id})
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_status_default_from_the_client_context_is_ignored(self):
        """FR-SPM-0011: a context default never creates a contact with a status."""
        partner = self.env['res.partner'].with_user(self.buyer).with_context(
            default_spm_status_id=self.blocked.id).create({'name': 'Sneaky'})
        self.assertFalse(partner.spm_status_id)

    def test_manager_cannot_change_a_contact_of_another_company(self):
        """FR-SPM-0011: the elevated write never lifts the company record rule."""
        company_b = self.env['res.company'].create({'name': 'Company B'})
        hidden = self.env['res.partner'].create({'name': 'Hidden', 'company_id': company_b.id})
        manager = self.manager.with_context(allowed_company_ids=self.manager.company_id.ids)
        with self.assertRaises(AccessError):
            hidden.with_user(manager).action_spm_set_status(self.blocked, 'Fraud')
        self.assertFalse(hidden.spm_status_id)

    def test_type_write_keeps_no_history_for_an_employee_contact(self):
        """BR-SPM-0004: writing a supplier type with an address on an employee's contact logs nothing."""
        employee = self.env['hr.employee'].create({'name': 'Eve'})
        contact = employee.work_contact_id
        supplier_type = self.env['procurement.supplier.type'].search([], limit=1) or \
            self.env['procurement.supplier.type'].create({'name': 'Goods'})
        contact.write({'spm_supplier_type_id': supplier_type.id, 'city': 'Paris'})
        self.assertFalse(contact.spm_change_ids)

    def test_status_action_accepts_an_id_and_refuses_an_unknown_one(self):
        """FR-SPM-0011: a direct call with an id works; a wrong id is a named refusal."""
        self.acme.with_user(self.manager).action_spm_set_status(self.blocked.id, 'Fraud')
        self.assertEqual(self.acme.spm_status_id, self.blocked)
        with self.assertRaises(UserError):
            self.acme.with_user(self.manager).action_spm_set_status(0, 'Fraud')
