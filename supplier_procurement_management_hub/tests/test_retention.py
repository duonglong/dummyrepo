"""Unit tests of the retention rules (T-035).

Requirements: FR-SPM-0110
"""
from freezegun import freeze_time

from odoo.tests.common import TransactionCase

from ..services import retention


class TestRetentionRules(TransactionCase):
    """Requirements: FR-SPM-0110"""

    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.company.write({'spm_portal_inactivity_days': 365, 'spm_document_retention_years': 7})
        self.acme = self.env['res.partner'].create({'name': 'Acme T035u', 'is_company': True})
        self.doc_type = self.env['procurement.document.type'].create({'name': 'Cert T035u', 'needs_expiry': True})

    def _document(self, **values):
        attachment = self.env['ir.attachment'].create({'name': 'f.pdf', 'raw': b'x'})
        return self.env['procurement.supplier.document'].create(dict({
            'partner_id': self.acme.id, 'document_type_id': self.doc_type.id,
            'attachment_id': attachment.id}, **values))

    def test_period_zero_switches_documents_off(self):
        """A retention period of 0 years deletes nothing."""
        self.company.spm_document_retention_years = 0
        doc = self._document(expiry_date='2000-01-01')
        with freeze_time('2026-11-17 08:00:00'):
            self.assertFalse(retention.purgeable_documents(self.env, self.company))
        self.assertTrue(doc.attachment_id)

    def test_period_zero_switches_users_off(self):
        """An inactivity period of 0 days deactivates nobody."""
        self.company.spm_portal_inactivity_days = 0
        self.assertFalse(retention.idle_portal_users(self.env, self.company))

    def test_current_expired_document_is_found_after_the_period(self):
        """A current document expired 7 years ago is purgeable, one expired 6 years ago is not."""
        old = self._document(expiry_date='2019-10-31')
        recent = self._document(expiry_date='2020-10-31')
        with freeze_time('2026-11-17 08:00:00'):
            found = retention.purgeable_documents(self.env, self.company)
        self.assertIn(old, found)
        self.assertNotIn(recent, found)

    def test_document_of_old_rejected_application_is_found(self):
        """The file of a document of an application rejected long ago is purgeable."""
        doc = self._document()
        application = self.env['procurement.supplier.application'].create({
            'email': 'a@t035u.example', 'company_id': self.company.id,
            'supplier_type_id': self.env['procurement.supplier.type'].create({'name': 'Type T035u'}).id})
        doc.write({'application_id': application.id})
        application.sudo().write({'state': 'rejected', 'rejection_reason': 'no'})
        self.env.flush_all()
        self.env.cr.execute("UPDATE procurement_supplier_application SET write_date = %s WHERE id = %s",
                            ['2018-01-01', application.id])
        self.env.invalidate_all()
        with freeze_time('2026-11-17 08:00:00'):
            self.assertIn(doc, retention.purgeable_documents(self.env, self.company))
