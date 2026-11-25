"""Acceptance tests of T-008 (job runner and daily re-qualification), written
from the specification.

Requirements: FR-SPM-0012, FR-SPM-0096
"""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestJobRunnerAcceptance(TransactionCase):
    """Requirements: FR-SPM-0012, FR-SPM-0096"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.today = fields.Date.context_today(self.env['res.partner'])
        self.buyer = self.env['res.users'].create({
            'name': 'Bea Buyer',
            'login': 'spm_buyer_t008',
            'email': 'spm_buyer_t008@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_buyer').id])],
        })
        self.supplier_type = self.env['procurement.supplier.type'].create({'name': 'Goods T008'})
        self.approved = self.env['procurement.supplier.status'].search(
            [('kind', '=', 'approved')], limit=1)

    def _job_code(self):
        selection = dict(self.env['procurement.job']._fields['code'].selection)
        keys = [k for k in selection if 'qualif' in k]
        self.assertTrue(keys, "a re-qualification job exists")
        return keys[0]

    def _supplier(self, requalification_date):
        return self.env['res.partner'].create({
            'name': 'Acme', 'is_company': True, 'street': 'Main Street 1',
            'spm_supplier_type_id': self.supplier_type.id,
            'spm_status_id': self.approved.id,
            'spm_requalification_date': requalification_date,
            'spm_responsible_user_id': self.buyer.id,
        })

    def _applications(self, acme):
        return self.env['procurement.supplier.application'].search([
            ('partner_id', '=', acme.id), ('is_requalification', '=', True)])

    def test_sc_0012_1_due_supplier_goes_to_requalification(self):
        """SC-SPM-0012-1: Acme is Approved with re-qualification date today;
        the daily job sets a status of kind Re-qualification due, creates a
        re-qualification application prefilled with its data and gives its
        buyer an activity."""
        acme = self._supplier(self.today)
        self.env['procurement.job']._spm_run(self._job_code())
        self.env.flush_all()
        acme.invalidate_recordset()
        self.assertEqual(acme.spm_status_kind, 'requalification_due')
        applications = self._applications(acme)
        self.assertEqual(len(applications), 1)
        self.assertEqual(applications.legal_name, 'Acme')
        self.assertEqual(applications.street, 'Main Street 1')
        activities = self.env['mail.activity'].search([
            ('user_id', '=', self.buyer.id),
            '|',
            '&', ('res_model', '=', 'res.partner'), ('res_id', '=', acme.id),
            '&', ('res_model', '=', 'procurement.supplier.application'),
            ('res_id', 'in', applications.ids),
        ])
        self.assertTrue(activities)

    def test_sc_0012_1_past_due_date_is_also_picked_up(self):
        """FR-SPM-0012: a re-qualification date earlier than today is due too."""
        acme = self._supplier(self.today - timedelta(days=5))
        self.env['procurement.job']._spm_run(self._job_code())
        self.env.flush_all()
        acme.invalidate_recordset()
        self.assertEqual(acme.spm_status_kind, 'requalification_due')

    def test_future_date_is_not_picked_up(self):
        """FR-SPM-0012 boundary: a re-qualification date tomorrow is not due."""
        acme = self._supplier(self.today + timedelta(days=1))
        self.env['procurement.job']._spm_run(self._job_code())
        self.env.flush_all()
        acme.invalidate_recordset()
        self.assertEqual(acme.spm_status_kind, 'approved')
        self.assertFalse(self._applications(acme))

    def test_sc_0012_2_second_run_creates_no_second_application(self):
        """SC-SPM-0012-2: a second run on the same day creates no second application."""
        acme = self._supplier(self.today)
        code = self._job_code()
        self.env['procurement.job']._spm_run(code)
        self.env['procurement.job']._spm_run(code)
        self.env.flush_all()
        self.assertEqual(len(self._applications(acme)), 1)

    def test_fr_0096_run_is_logged(self):
        """FR-SPM-0096: a run is logged with start, end, records processed and outcome."""
        self._supplier(self.today)
        code = self._job_code()
        self.env['procurement.job']._spm_run(code)
        self.env.flush_all()
        job = self.env['procurement.job'].search([('code', '=', code)])
        run = job.last_run_id
        self.assertTrue(run)
        self.assertEqual(run.state, 'done')
        self.assertTrue(run.date_start)
        self.assertTrue(run.date_end)
        self.assertGreaterEqual(run.processed_count, 1)
        self.assertEqual(run.error_count, 0)

    def test_sc_0096_2_switched_off_job_has_its_scheduled_action_off(self):
        """SC-SPM-0096-2: an Administrator switches a job off; its scheduled
        action is then inactive, so its scheduled time does not run it, and
        switching it back on activates it again. (The scheduler itself is
        Odoo's: an inactive scheduled action is never picked.)"""
        job = self.env['procurement.job'].search([('code', '=', self._job_code())])
        job.write({'active': False})
        self.env.flush_all()
        self.assertFalse(job.cron_id.active)
        self.assertFalse(self.env['ir.cron'].search([('id', '=', job.cron_id.id)]))
        job.write({'active': True})
        self.assertTrue(job.cron_id.active)
