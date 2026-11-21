"""A job run twice sends and logs once (US-SPM-010: reminders run once per due event)."""
from datetime import timedelta

from odoo import fields

from .delivery_common import SpmDeliveryCase


class TestJobsRepeat(SpmDeliveryCase):
    """A repeated run adds nothing."""

    def test_the_document_job_run_twice_notifies_once(self):
        """A second run of the expiry job on the same day adds no key, no message and no to-do."""
        self.acme.spm_responsible_user_id = self.buyer
        doc_type = self.env['procurement.document.type'].create(
            {'name': 'Insurance repeat', 'reminder_days': 30, 'needs_expiry': True})
        self.env['procurement.supplier.document'].with_user(self.buyer).create({
            'partner_id': self.acme.id, 'document_type_id': doc_type.id,
            'issue_date': fields.Date.today() - timedelta(days=300),
            'expiry_date': fields.Date.today() + timedelta(days=10), 'verification_state': 'verified'})
        job = self.env['procurement.job'].search([('code', '=', 'document_expiry')])
        keys = self.env['procurement.notification.log']
        activities = self.env['mail.activity'].sudo()  # sudo: test reads every to-do

        job._spm_run('document_expiry', trigger='manual')
        first = (keys.search_count([]), activities.search_count([]))
        self.assertGreater(first[0], 0)
        job._spm_run('document_expiry', trigger='manual')
        self.assertEqual((keys.search_count([]), activities.search_count([])), first)
        self.assertEqual(job.run_ids[:1].state, 'done')
