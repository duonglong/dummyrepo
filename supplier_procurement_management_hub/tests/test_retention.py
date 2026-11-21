"""Unit tests of the retention rules (T-035).

Requirements: FR-SPM-0110
"""
from unittest.mock import patch

from freezegun import freeze_time

from odoo.tests.common import TransactionCase

from ..services import retention


class TestRetentionRules(TransactionCase):
    """Requirements: FR-SPM-0110"""

    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.company.write({'spm_portal_inactivity_days': 365, 'spm_document_retention_years': 7})
        self.acme = self.env['res.partner'].create({'name': 'Acme T035u', 'is_company': True, 'company_id': self.company.id})
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

    def _idle_user(self):
        contact = self.env['res.partner'].create({'name': 'Idle T035u', 'parent_id': self.acme.id})
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'partner_id': contact.id, 'login': 'idle_t035u', 'password': 'spm-portal-pass-T035',
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])]})

    def test_period_zero_switches_users_off(self):
        """An idle user is found at 365 days and not at 0 days."""
        user = self._idle_user()
        with freeze_time('2030-01-01 08:00:00'):
            self.assertIn(user, retention.idle_portal_users(self.env, self.company))
            self.company.spm_portal_inactivity_days = 0
            self.assertFalse(retention.idle_portal_users(self.env, self.company))

    def test_rejected_document_follows_its_review_date(self):
        """A current rejected document reviewed 7 years ago is found, one reviewed 6 years ago is not."""
        old = self._document(verification_state='rejected', review_comment='no', expiry_date='2099-01-01')
        recent = self._document(verification_state='rejected', review_comment='no', expiry_date='2099-01-01')
        self.env.flush_all()
        for doc, reviewed in ((old, '2019-10-31'), (recent, '2020-10-31')):
            self.env.cr.execute("UPDATE procurement_supplier_document SET review_date = %s WHERE id = %s",
                                [reviewed, doc.id])
        self.env.invalidate_all()
        with freeze_time('2026-11-17 08:00:00'):
            found = retention.purgeable_documents(self.env, self.company)
        self.assertIn(old, found)
        self.assertNotIn(recent, found)

    def test_shared_supplier_uses_the_longest_period_and_one_company_off_keeps_all(self):
        """Documents of a supplier with no company follow the longest period; a company with 0 switches it off."""
        self.acme.company_id = False
        other = self.env['res.company'].create({'name': 'Other T035u', 'spm_document_retention_years': 3})
        doc = self._document(expiry_date='2019-10-31')
        self.assertEqual(retention.shared_retention_years(self.env),
                         max(self.env['res.company'].search([]).mapped('spm_document_retention_years')))
        with freeze_time('2026-11-17 08:00:00'):
            self.assertIn(doc, retention.purgeable_documents(self.env, None))
            self.assertNotIn(doc, retention.purgeable_documents(self.env, self.company))
            other.spm_document_retention_years = 0
            self.assertFalse(retention.purgeable_documents(self.env, None))

    def test_one_failing_document_does_not_stop_the_others(self):
        """The runner gives each record its own savepoint: a failing file leaves the other purged."""
        bad = self._document(expiry_date='2019-10-31')
        good = self._document(expiry_date='2019-10-31')
        good_attachment = good.attachment_id
        job = self.env['procurement.job'].search([('code', '=', 'retention')], limit=1) or \
            self.env['procurement.job'].create({'name': 'Retention T035u', 'code': 'retention'})
        original = type(job)._spm_process_retention

        def process(self_, record):
            if record == bad:
                raise ValueError('boom')
            return original(self_, record)

        with freeze_time('2026-11-17 08:00:00'), patch.object(type(job), '_spm_process_retention', process):
            run = job._spm_run('retention')
        self.env.invalidate_all()
        self.assertFalse(good_attachment.exists())
        self.assertTrue(bad.attachment_id)
        self.assertEqual([e['id'] for e in run.errors], [bad.id])

    def test_audit_entry_keeps_the_deleted_file_name(self):
        """The audit entry of a deleted file names the file."""
        doc = self._document(expiry_date='2019-10-31')
        job = self.env['procurement.job'].new({'code': 'retention'})
        job._spm_process_retention(doc)
        entry = self.env['procurement.audit.log'].sudo().search([('res_model', '=', doc._name), ('res_id', '=', doc.id)])
        self.assertIn('f.pdf', str(entry.old_values))

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
