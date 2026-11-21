"""Acceptance tests of T-035 (retention job), written from the specification.

Requirements: FR-SPM-0110

Names the design does not fix, chosen here (see the module notes):
- the job is found by a word of its ``procurement.job`` code (retention, retain, purge, inactiv) and run
  with ``procurement.job._spm_run(code)``;
- the periods are the company settings ``spm_portal_inactivity_days`` and ``spm_document_retention_years``;
- the last login of a user is its newest ``res.users.log`` row; the replaced date of a document is
  ``replaced_date``; "the file is deleted" means the attachment no longer exists;
- the audit entry of a deactivated user is any ``procurement.audit.log`` row of the user or of its contact.
"""
from freezegun import freeze_time

from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'
WORDS = ('retention', 'retain', 'purge', 'inactiv')


class TestRetentionJob(TransactionCase):
    """Requirements: FR-SPM-0110"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        env = self.env
        self.company = env.company
        self.company.write({'spm_portal_inactivity_days': 365, 'spm_document_retention_years': 7})
        self.buyer = env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Bea Buyer T035', 'login': 'spm_buyer_t035', 'email': 'spm_buyer_t035@example.com',
            'group_ids': [(6, 0, [env.ref(f'{MODULE}.group_spm_buyer').id,
                                  env.ref('base.group_partner_manager').id])]})
        self.acme = env['res.partner'].create({'name': 'Acme T035', 'is_company': True})
        self.insurance = env['procurement.document.type'].create({
            'name': 'Insurance T035', 'reminder_days': 30, 'needs_expiry': True})

    def _portal_user(self, login, last_login):
        contact = self.env['res.partner'].create({
            'name': login, 'parent_id': self.acme.id, 'type': 'contact', 'email': f'{login}@supplier.example'})
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'partner_id': contact.id, 'login': login, 'password': 'spm-portal-pass-T035',
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])]})
        self._set_last_login(user, last_login)
        return user

    def _set_last_login(self, user, when):
        """Leave one login row of ``user`` dated ``when`` (create_date is not frozen by freezegun: SQL)."""
        self.env['res.users.log'].sudo().search([('create_uid', '=', user.id)]).unlink()
        log = self.env['res.users.log'].sudo().create({})
        self.env.cr.execute(
            "UPDATE res_users_log SET create_uid = %s, create_date = %s WHERE id = %s",
            [user.id, when, log.id])
        self.env.invalidate_all()

    def _run(self, when):
        codes = [c for c, _label in self.env['procurement.job']._fields['code'].selection
                 if any(w in c for w in WORDS)]
        self.assertTrue(codes, "a retention job exists")
        with freeze_time(when):
            for code in codes:
                self.env['procurement.job']._spm_run(code)
        self.env.flush_all()
        self.env.invalidate_all()

    def _audit_of(self, user):
        return self.env['procurement.audit.log'].sudo().search([
            '|', '&', ('res_model', '=', 'res.users'), ('res_id', '=', user.id),
            '&', ('res_model', '=', 'res.partner'), ('res_id', '=', user.partner_id.id)])

    def _document(self, replaced, **extra):
        attachment = self.env['ir.attachment'].with_user(self.buyer).create({
            'name': 'insurance.pdf', 'raw': b'%PDF-1.4 certificate', 'mimetype': 'application/pdf'})
        doc = self.env['procurement.supplier.document'].with_user(self.buyer).create(dict({
            'partner_id': self.acme.id, 'document_type_id': self.insurance.id,
            'issue_date': '2018-01-01', 'expiry_date': '2019-10-31',
            'verification_state': 'verified', 'attachment_id': attachment.id}, **extra))
        doc.sudo().write({'is_current': False, 'replaced_date': replaced})
        self.env.flush_all()
        return doc, attachment

    # FR-SPM-0110 portal accounts ------------------------------------------
    def test_sc_0110_1_idle_portal_user_is_deactivated_with_audit(self):
        """SC-SPM-0110-1: an Acme portal user whose last login was 2025-11-16; the retention job of
        2026-11-17 deactivates the user and an audit entry records it."""
        user = self._portal_user('spm_idle_t035', '2025-11-16 10:00:00')
        before = len(self._audit_of(user))
        self._run('2026-11-17 08:00:00')
        self.assertFalse(user.active)
        self.assertGreater(len(self._audit_of(user)), before)

    def test_sc_0110_1_recent_login_keeps_the_user(self):
        """FR-SPM-0110 boundary: a login 364 days before the job (2025-11-18) keeps the account."""
        user = self._portal_user('spm_recent_t035', '2025-11-18 10:00:00')
        self._run('2026-11-17 08:00:00')
        self.assertTrue(user.active)

    def test_sc_0110_1_period_is_a_company_setting(self):
        """FR-SPM-0110: each period is a company setting: with 30 days a login of 2026-10-01 is idle."""
        self.company.spm_portal_inactivity_days = 30
        user = self._portal_user('spm_setting_t035', '2026-10-01 10:00:00')
        self._run('2026-11-17 08:00:00')
        self.assertFalse(user.active)

    def test_sc_0110_1_internal_users_are_not_touched(self):
        """FR-SPM-0110: only supplier portal users are deactivated, never an internal user."""
        self._set_last_login(self.buyer, '2020-01-01 10:00:00')
        self._run('2026-11-17 08:00:00')
        self.assertTrue(self.buyer.active)

    def test_sc_0110_1_second_run_changes_nothing_more(self):
        """FR-SPM-0110: a second run adds no second audit entry for the same user."""
        user = self._portal_user('spm_twice_t035', '2025-11-16 10:00:00')
        self._run('2026-11-17 08:00:00')
        first = len(self._audit_of(user))
        self._run('2026-11-18 08:00:00')
        self.assertEqual(len(self._audit_of(user)), first)

    # FR-SPM-0110 supplier files -------------------------------------------
    def test_sc_0110_2_old_replaced_file_is_deleted_metadata_kept(self):
        """SC-SPM-0110-2: a replaced insurance certificate replaced on 2019-11-01; the job of 2026-11-17
        deletes its file; its type, dates, verification status and audit entries remain."""
        doc, attachment = self._document('2019-11-01 10:00:00')
        entry = self.env['procurement.audit.log'].sudo().create({
            'event': 'configuration', 'res_model': doc._name, 'res_id': doc.id, 'record_name': 'doc'})
        attachment_id = attachment.id
        self._run('2026-11-17 08:00:00')
        self.assertFalse(self.env['ir.attachment'].sudo().browse(attachment_id).exists(), "the file is deleted")
        self.assertTrue(doc.exists(), "the record stays")
        self.assertEqual(doc.document_type_id, self.insurance)
        self.assertEqual(str(doc.issue_date), '2018-01-01')
        self.assertEqual(str(doc.expiry_date), '2019-10-31')
        self.assertEqual(doc.verification_state, 'verified')
        self.assertTrue(entry.exists(), "the audit entry stays")

    def test_sc_0110_2_recent_replaced_file_is_kept(self):
        """FR-SPM-0110 boundary: a file replaced on 2019-11-30, just under 7 years before the job, is kept."""
        doc, attachment = self._document('2019-11-30 10:00:00')
        self._run('2026-11-17 08:00:00')
        self.assertTrue(self.env['ir.attachment'].sudo().browse(attachment.id).exists())

    def test_sc_0110_2_period_is_a_company_setting(self):
        """FR-SPM-0110: with a 3-year retention a file replaced on 2022-11-01 is deleted."""
        self.company.spm_document_retention_years = 3
        doc, attachment = self._document('2022-11-01 10:00:00')
        self._run('2026-11-17 08:00:00')
        self.assertFalse(self.env['ir.attachment'].sudo().browse(attachment.id).exists())
        self.assertTrue(doc.exists())
