"""Own tests of the job runner (T-008).

Requirements: FR-SPM-0012, FR-SPM-0096, FR-SPM-0097
"""
from ast import literal_eval
from unittest.mock import patch

from odoo import fields, modules, sql_db
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase

from ..services import job_runner

MODULE = 'supplier_procurement_management_hub'


class TestJobRunner(TransactionCase):
    """Requirements: FR-SPM-0012, FR-SPM-0096, FR-SPM-0097"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.job = self.env['procurement.job'].search([('code', '=', 'requalification')])
        self.admin = self.env['res.users'].create({
            'name': 'Ada Admin', 'login': 'spm_admin_t008', 'email': 'spm_admin_t008@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_admin').id])]})
        self.buyer = self.env['res.users'].create({
            'name': 'Bea Buyer', 'login': 'spm_buyer_t008u', 'email': 'spm_buyer_t008u@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_buyer').id])]})

    def test_the_job_and_its_scheduled_action_exist(self):
        """FR-SPM-0096: each job has its scheduled action."""
        self.assertTrue(self.job.cron_id)
        self.assertTrue(self.job.cron_id.active)

    def test_only_an_administrator_runs_a_job_now(self):
        """FR-SPM-0096: Run now is refused to a buyer and works for an administrator."""
        with self.assertRaises(AccessError):
            self.job.with_user(self.buyer).action_spm_run_now()
        action = self.job.with_user(self.admin).action_spm_run_now()
        run = self.env['procurement.job.run'].browse(action['res_id'])
        self.assertEqual((run.state, run.trigger), ('done', 'manual'))

    def test_a_job_switched_off_can_still_run_now(self):
        """FR-SPM-0096: switching off stops the schedule only."""
        self.job.write({'active': False})
        run = self.job.with_user(self.admin).action_spm_run_now()
        self.assertTrue(run['res_id'])

    def test_only_the_switch_of_a_job_changes_by_hand(self):
        """FR-SPM-0096: an administrator cannot rename or re-code a job."""
        with self.assertRaises(AccessError):
            self.job.with_user(self.admin).write({'name': 'Other'})
        self.job.with_user(self.admin).write({'active': False})
        self.assertFalse(self.job.cron_id.active)

    def test_a_whole_run_failure_is_logged_as_failed(self):
        """FR-SPM-0096: an error outside any record closes the run as failed."""
        with patch.object(type(self.job), '_spm_collect_requalification', side_effect=ValueError('boom')):
            run = self.env['procurement.job']._spm_run('requalification')
        self.assertEqual(run.state, 'failed')
        self.assertEqual(run.error_count, 1)
        self.assertIn('boom', run.error_summary)

    def test_a_stuck_run_can_be_closed_by_an_administrator_only(self):
        """FR-SPM-0097: a run left Running blocks the job until an administrator closes it."""
        stuck = self.env['procurement.job.run'].create({
            'job_id': self.job.id, 'state': 'running', 'date_start': fields.Datetime.now()})
        with self.assertRaises(AccessError):
            stuck.with_user(self.buyer).action_spm_mark_failed()
        stuck.with_user(self.admin).action_spm_mark_failed()
        self.assertEqual(stuck.state, 'failed')
        with self.assertRaises(UserError):
            stuck.with_user(self.admin).action_spm_mark_failed()
        run = self.env['procurement.job']._spm_run('requalification')
        self.assertEqual(run.state, 'done')

    def test_an_unknown_code_is_refused(self):
        """FR-SPM-0096: a scheduled action with a wrong code raises."""
        with self.assertRaises(UserError):
            self.env['procurement.job']._spm_run('nothing')

    def test_a_supplier_with_an_open_requalification_application_is_not_due_again(self):
        """FR-SPM-0012: no second application while one is open."""
        approved = self.env['procurement.supplier.status'].search([('kind', '=', 'approved')], limit=1)
        supplier_type = self.env['procurement.supplier.type'].create({'name': 'Goods T008U'})
        acme = self.env['res.partner'].create({
            'name': 'Acme U', 'is_company': True, 'spm_supplier_type_id': supplier_type.id,
            'spm_status_id': approved.id, 'spm_requalification_date': fields.Date.context_today(self.job),
            'spm_responsible_user_id': self.buyer.id})
        self.env['procurement.job']._spm_run('requalification')
        acme.write({'spm_status_id': approved.id})
        self.env['procurement.job']._spm_run('requalification')
        applications = self.env['procurement.supplier.application'].search(
            [('partner_id', '=', acme.id), ('is_requalification', '=', True)])
        self.assertEqual(len(applications), 1)

    def _due_supplier(self, **extra):
        approved = self.env['procurement.supplier.status'].search([('kind', '=', 'approved')], limit=1)
        supplier_type = self.env['procurement.supplier.type'].create({'name': 'Goods T008Q'})
        return self.env['res.partner'].create(dict({
            'name': 'Quiet Co', 'is_company': True, 'spm_supplier_type_id': supplier_type.id,
            'spm_status_id': approved.id, 'spm_requalification_date': fields.Date.context_today(self.job),
            'spm_responsible_user_id': self.buyer.id}, **extra))

    def test_a_switched_off_job_stays_on_the_jobs_screen(self):
        """FR-SPM-0096: the jobs action lists a switched-off job, so it can be switched back on."""
        self.job.write({'active': False})
        action = self.env.ref(f'{MODULE}.procurement_job_action')
        listed = self.env['procurement.job'].with_context(literal_eval(action.context)).search([])
        self.assertIn(self.job, listed)

    def test_approving_a_requalification_keeps_the_contact_and_its_empty_email(self):
        """FR-SPM-0012: approval updates the supplier named by the application and never copies the buyer's address."""
        acme = self._due_supplier()
        self.env['procurement.job']._spm_run('requalification')
        application = self.env['procurement.supplier.application'].search([('partner_id', '=', acme.id)])
        self.assertEqual(application.email, self.buyer.email)
        partners = self.env['res.partner'].search_count([('name', '=', 'Quiet Co')])
        self.assertEqual(application._spm_contact_for_approval(), acme)
        self.assertEqual(self.env['res.partner'].search_count([('name', '=', 'Quiet Co')]), partners)
        self.assertFalse(acme.email)

    def test_run_now_commits_only_inside_the_scheduler_loop(self):
        """NFR-SPM-0007: the request of Run now keeps one transaction; the cron loop reports its progress."""
        self._due_supplier()
        with patch.object(modules.module, 'current_test', None), \
                patch.object(type(self.env['ir.cron']), '_commit_progress') as progress:
            self.env['procurement.job']._spm_run('requalification', trigger='manual')
            progress.assert_not_called()
            self.env['procurement.job'].with_context(ir_cron_progress_id=1)._spm_run('requalification')
            progress.assert_called()

    def test_a_run_started_while_another_cursor_holds_the_lock_is_skipped(self):
        """NFR-SPM-0007: two runs at the same moment; the second is skipped and logged."""
        other = sql_db.db_connect(self.env.cr.dbname).cursor()
        self.addCleanup(other.close)
        other.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", [job_runner._lock_key(self.job)])
        run = self.env['procurement.job']._spm_run('requalification')
        self.assertEqual(run.state, 'skipped')
        other.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", [job_runner._lock_key(self.job)])
        self.assertEqual(self.env['procurement.job']._spm_run('requalification').state, 'done')
