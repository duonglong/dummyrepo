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
