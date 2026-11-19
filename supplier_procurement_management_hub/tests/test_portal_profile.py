"""Own tests of T-011: the pieces the acceptance tests do not reach.

Requirements: FR-SPM-0015, FR-SPM-0018, FR-SPM-0019, NFR-SPM-0003
"""
from .portal_common import SpmPortalCase


class TestPortalProfile(SpmPortalCase):
    """Requirements: FR-SPM-0015, FR-SPM-0018, FR-SPM-0019"""

    def setUp(self):
        super().setUp()
        self.phone = self.env['ir.model.fields']._get('res.partner', 'phone')
        self.env['procurement.portal.field'].create({'field_id': self.phone.id, 'editable': True})
        self.insurance = self.env['procurement.document.type'].create({'name': 'Cert T11'})

    def test_profile_page_lists_the_editable_field(self):
        """FR-SPM-0018: the profile page shows the editable phone field."""
        self._login(self.acme_user)
        response = self._get('/my/procurement/profile')
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="phone"', response.text)

    def test_newer_bank_change_replaces_the_pending_one(self):
        """FR-SPM-0015: only the last pending change of a supplier waits."""
        self._login(self.acme_user)
        self._post('/my/procurement/profile', {'bank_account_number': 'BE33 3333'})
        self._post('/my/procurement/profile', {'bank_account_number': 'BE44 4444'})
        changes = self.env['procurement.supplier.change'].sudo().search(
            [('partner_id', '=', self.acme.id), ('field_name', '=', 'bank_ids')])
        self.assertEqual(sorted(changes.mapped('state')), ['pending', 'refused'])

    def test_foreign_user_cannot_upload_for_another_supplier(self):
        """FR-SPM-0019: the supplier is the session user's, never a posted value."""
        self._login(self.beta_user)
        self._post('/my/procurement/documents/upload', {
            'document_type_id': self.insurance.id, 'partner_id': self.acme.id,
        }, files=[('file', ('a.pdf', b'%PDF-1.4\n0000', 'application/pdf'))])
        docs = self.env['procurement.supplier.document'].sudo().search(
            [('document_type_id', '=', self.insurance.id)])
        self.assertEqual(docs.partner_id, self.beta)

    def _pending(self):
        return self.env['procurement.supplier.change'].sudo().search(
            [('partner_id', '=', self.acme.id), ('field_name', '=', 'bank_ids'), ('state', '=', 'pending')])

    def _finance_verify(self, change):
        finance = self.env['res.users'].create({
            'name': 'Fin T11', 'login': 'fin_t11_%s' % change.id,
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('supplier_procurement_management_hub.group_spm_finance').id])]})
        change.with_user(finance).action_spm_verify()

    def test_same_number_with_a_new_holder_is_held(self):
        """FR-SPM-0015: a known number with another holder is a change, not dropped."""
        self.env['res.partner.bank'].create({
            'partner_id': self.acme.id, 'account_number': 'BE11 1111', 'holder_name': 'Old'})
        self._login(self.acme_user)
        self._post('/my/procurement/profile', {'bank_account_number': 'BE11 1111', 'bank_holder': 'New Holder'})
        self.assertEqual(len(self._pending()), 1)
        self._post('/my/procurement/profile', {'bank_account_number': 'BE11 1111', 'bank_holder': 'Old'})
        self.assertEqual(len(self._pending()), 1, 'an identical account is no change')

    def test_number_with_other_punctuation_is_the_same_number(self):
        """FR-SPM-0015: BE11-1111 is the known BE11 1111 (native sanitized number), and verifies cleanly."""
        bank = self.env['res.partner.bank'].create({
            'partner_id': self.acme.id, 'account_number': 'BE11 1111', 'holder_name': 'Old'})
        self._login(self.acme_user)
        self._post('/my/procurement/profile', {'bank_account_number': 'BE11-1111', 'bank_holder': 'Old'})
        self.assertEqual(len(self._pending()), 0)
        self._post('/my/procurement/profile', {'bank_account_number': 'BE11-1111', 'bank_holder': 'New'})
        self._finance_verify(self._pending())
        self.assertEqual(bank.holder_name, 'New')
        self.assertEqual(len(self.acme.bank_ids), 1)

    def test_verified_account_is_trusted_and_archived_number_is_reused(self):
        """FR-SPM-0015: a verified account can be paid; an archived number is activated."""
        old = self.env['res.partner.bank'].create({
            'partner_id': self.acme.id, 'account_number': 'BE55 5555', 'holder_name': 'Old'})
        old.active = False
        self._login(self.acme_user)
        self._post('/my/procurement/profile', {'bank_account_number': 'BE55 5555', 'bank_holder': 'Back'})
        self.assertEqual(len(self._pending()), 1)
        self._finance_verify(self._pending())
        self.assertTrue(old.active)
        self.assertTrue(old.allow_out_payment)
        self.assertEqual(old.holder_name, 'Back')
        self._post('/my/procurement/profile', {'bank_account_number': 'BE66 6666'})
        self._finance_verify(self._pending())
        new = self.acme.bank_ids.filtered(lambda b: b.sanitized_account_number == 'BE666666')
        self.assertTrue(new.allow_out_payment)

    def test_unknown_form_keys_are_logged(self):
        """FR-SPM-0018: unknown keys are ignored and their names logged."""
        self._login(self.acme_user)
        self._post('/my/procurement/profile', {'phone': '123', 'credit_limit': '9'})
        logs = self.env['procurement.portal.log'].sudo().search([('detail', 'like', 'ignored keys')])
        self.assertTrue(logs)
        self.assertIn('credit_limit', logs[0].detail)
        self.assertNotIn('phone', logs[0].detail)
        self._post('/my/procurement/documents/upload', {
            'document_type_id': self.insurance.id, 'role': 'x',
        }, files=[('file', ('a.pdf', b'%PDF-1.4\n0000', 'application/pdf'))])
        self.assertTrue(self.env['procurement.portal.log'].sudo().search(
            [('detail', 'like', 'upload ignored keys: role')]))
