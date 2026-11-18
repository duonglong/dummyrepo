"""Acceptance tests of T-011 (portal profile edit, document upload, bank-change
hold), written from the specification.

Requirements: FR-SPM-0015, FR-SPM-0018, FR-SPM-0019, NFR-SPM-0003

The portal pages are driven with the HTTP client: POST /my/procurement/profile
(form keys are the contact field names plus bank_account_number, bank_name,
bank_bic, bank_holder) and POST /my/procurement/documents/upload (document_type_id,
issue_date, expiry_date and the multipart file ``file``). The verification step is
procurement.supplier.change.action_spm_verify(); the design names the buttons only.
"""
from odoo.exceptions import AccessError

from .portal_common import SpmPortalCase

PDF = b'%PDF-1.4\n' + b'0' * (2 * 1024 * 1024)


def _plain(number):
    return (number or '').replace(' ', '').upper()


class TestPortalProfileAcceptance(SpmPortalCase):
    """Requirements: FR-SPM-0015, FR-SPM-0018"""

    def setUp(self):
        super().setUp()
        self.bank = self.env['res.partner.bank'].create({
            'partner_id': self.acme.id, 'account_number': 'BE11 1111'})
        self.phone_field = self.env['ir.model.fields']._get('res.partner', 'phone')
        self.env['procurement.portal.field'].create({
            'field_id': self.phone_field.id, 'editable': True})
        self.change_model = self.env['procurement.supplier.change']

    def _pending(self):
        return self.change_model.sudo().search([
            ('partner_id', '=', self.acme.id), ('state', '=', 'pending')])

    def _banks(self):
        return [_plain(b.account_number) for b in self.acme.bank_ids]

    def test_sc_0015_1_portal_bank_change_waits_for_verification(self):
        """SC-SPM-0015-1: Acme keeps BE11 1111 and a pending change to BE22 2222 waits."""
        self._login(self.acme_user)
        self._post('/my/procurement/profile', {'bank_account_number': 'BE22 2222'})
        self.env.flush_all()
        self.acme.invalidate_recordset()
        self.assertEqual(self._banks(), ['BE111111'])
        pending = self._pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(_plain(pending.bank_account_number), 'BE222222')
        self.assertEqual(pending.origin, 'portal')

    def test_sc_0015_2_finance_verifies_the_pending_change(self):
        """SC-SPM-0015-2: after a Finance reviewer verifies it, BE22 2222 is
        Acme's bank account and the change history shows the verifier."""
        self._login(self.acme_user)
        self._post('/my/procurement/profile', {'bank_account_number': 'BE22 2222'})
        pending = self._pending()
        self.assertTrue(pending)
        pending.with_user(self.finance).action_spm_verify()
        self.env.flush_all()
        self.acme.invalidate_recordset()
        self.assertIn('BE222222', self._banks())
        self.assertEqual(pending.state, 'verified')
        self.assertEqual(pending.verified_by_id, self.finance)
        self.assertTrue(pending.verified_date)

    def test_fr_0015_a_buyer_cannot_verify_a_bank_change(self):
        """FR-SPM-0015: only a Finance reviewer or Procurement manager verifies."""
        self._login(self.acme_user)
        self._post('/my/procurement/profile', {'bank_account_number': 'BE22 2222'})
        pending = self._pending()
        self.assertTrue(pending)
        with self.assertRaises(AccessError):
            pending.with_user(self.buyer).action_spm_verify()
        self.assertEqual(pending.state, 'pending')
        self.assertEqual(self._banks(), ['BE111111'])

    def test_fr_0015_a_procurement_manager_verifies_the_change(self):
        """FR-SPM-0015: a Procurement manager may verify as well."""
        self._login(self.acme_user)
        self._post('/my/procurement/profile', {'bank_account_number': 'BE22 2222'})
        pending = self._pending()
        pending.with_user(self.manager).action_spm_verify()
        self.assertEqual(pending.verified_by_id, self.manager)
        self.assertIn('BE222222', self._banks())

    def test_sc_0018_1_only_marked_fields_are_written(self):
        """SC-SPM-0018-1: Phone is editable, Payment terms is not; a crafted
        form changes the phone only, and the change is logged with origin portal."""
        term = self.env.ref('account.account_payment_term_immediate')
        other = self.env.ref('account.account_payment_term_30days')
        self.acme.write({'property_supplier_payment_term_id': term.id})
        self._login(self.acme_user)
        self._post('/my/procurement/profile', {
            'phone': '+32 2 555 0100',
            'property_supplier_payment_term_id': other.id,
            'spm_status_id': self.blocked.id,
        })
        self.env.flush_all()
        self.acme.invalidate_recordset()
        self.assertEqual(self.acme.phone, '+32 2 555 0100')
        self.assertEqual(self.acme.property_supplier_payment_term_id, term)
        self.assertEqual(self.acme.spm_status_id, self.approved)
        rows = self.acme.spm_change_ids.filtered(lambda c: c.field_name == 'phone')
        self.assertEqual(rows.mapped('origin'), ['portal'])
        self.assertEqual(rows.mapped('user_id'), self.acme_user)

    def test_fr_0018_a_field_not_marked_editable_is_not_written(self):
        """FR-SPM-0018 boundary: a field listed read-only (editable off) is ignored."""
        self.env['procurement.portal.field'].search([('field_id', '=', self.phone_field.id)]).write(
            {'editable': False})
        street = self.env['ir.model.fields']._get('res.partner', 'street')
        self.env['procurement.portal.field'].create({'field_id': street.id, 'editable': True})
        self.acme.write({'phone': '111', 'street': 'Old Street 1'})
        self._login(self.acme_user)
        self._post('/my/procurement/profile', {'phone': '222', 'street': 'New Street 2'})
        self.acme.invalidate_recordset()
        self.assertEqual(self.acme.street, 'New Street 2')
        self.assertEqual(self.acme.phone, '111')


class TestPortalUploadAcceptance(SpmPortalCase):
    """Requirements: FR-SPM-0019, NFR-SPM-0003, FR-SPM-0017"""

    def setUp(self):
        super().setUp()
        self.insurance = self.env['procurement.document.type'].create({
            'name': 'Insurance M2', 'needs_expiry': True, 'reminder_days': 30})
        self.docs = self.env['procurement.supplier.document']
        self.old = self.docs.with_user(self.buyer).create({
            'partner_id': self.acme.id, 'document_type_id': self.insurance.id,
            'issue_date': '2026-01-01', 'expiry_date': '2026-12-31',
            'attachment_id': self.env['ir.attachment'].with_user(self.buyer).create({
                'name': 'insurance-2026.pdf', 'raw': PDF, 'mimetype': 'application/pdf'}).id,
        })
        self.old.with_user(self.buyer).write({'verification_state': 'verified'})

    def _upload(self, name, content, mimetype='application/pdf'):
        return self._post('/my/procurement/documents/upload', {
            'document_type_id': self.insurance.id,
            'issue_date': '2027-01-01', 'expiry_date': '2027-12-31',
        }, files=[('file', (name, content, mimetype))])

    def _acme_docs(self):
        return self.docs.sudo().with_context(active_test=False).search([
            ('partner_id', '=', self.acme.id), ('document_type_id', '=', self.insurance.id)])

    def test_sc_0019_1_replacement_keeps_the_earlier_version(self):
        """SC-SPM-0019-1: the 2027 PDF of 2 MB is stored To verify and the
        2026 file stays as an earlier version."""
        self._login(self.acme_user)
        self._upload('insurance-2027.pdf', PDF)
        self.env.flush_all()
        self.old.invalidate_recordset()
        new = self._acme_docs() - self.old
        self.assertEqual(len(new), 1)
        self.assertEqual(new.verification_state, 'to_verify')
        self.assertTrue(new.is_current)
        self.assertTrue(new.uploaded_from_portal)
        self.assertEqual(new.previous_version_id, self.old)
        self.assertFalse(self.old.is_current)
        self.assertEqual(self.old.attachment_id.name, 'insurance-2026.pdf')
        self.assertEqual(new.attachment_id.name, 'insurance-2027.pdf')

    def test_sc_0019_2_oversized_file_is_refused(self):
        """SC-SPM-0019-2: a 25 MB file is refused with a message giving the
        allowed formats and the size; nothing is stored."""
        self._login(self.acme_user)
        response = self._upload('big.pdf', b'%PDF-1.4\n' + b'0' * (25 * 1024 * 1024))
        self.assertEqual(self._acme_docs(), self.old)
        text = response.text
        for needle in ('PDF', 'PNG', 'JPEG', '20'):
            self.assertIn(needle, text)

    def test_sc_0019_2_executable_renamed_pdf_is_refused(self):
        """SC-SPM-0019-2 / NFR-SPM-0003: a file whose content is an executable
        renamed to .pdf is refused by its real content; nothing is stored."""
        self._login(self.acme_user)
        response = self._upload('invoice.pdf', b'MZ\x90\x00\x03\x00\x00\x00' + b'\x00' * 4096)
        self.assertEqual(self._acme_docs(), self.old)
        for needle in ('PDF', 'PNG', 'JPEG', '20'):
            self.assertIn(needle, response.text)

    def test_nfr_0003_png_and_jpeg_are_allowed(self):
        """FR-SPM-0019: PDF, PNG and JPEG are the allowed formats."""
        self._login(self.acme_user)
        png = b'\x89PNG\r\n\x1a\n' + b'\x00' * 256
        self._upload('scan.png', png, 'image/png')
        self.assertEqual(len(self._acme_docs()), 2)

    def test_nfr_0003_files_are_served_only_to_their_owner(self):
        """NFR-SPM-0003, FR-SPM-0017: Acme downloads its own document; Beta's
        user gets a not found answer and no content."""
        self.old.sudo().attachment_id.write({'res_model': self.old._name, 'res_id': self.old.id})
        self._login(self.acme_user)
        own = self._get(f'/my/procurement/file/{self.old.id}')
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.content[:5], b'%PDF-')
        self.authenticate(self.beta_user.login, 'spm-portal-pass')
        foreign = self._get(f'/my/procurement/file/{self.old.id}')
        self.assertEqual(foreign.status_code, 404)
        self.assertNotIn(b'%PDF-', foreign.content[:5])
