"""Own tests of T-027: cleanup, audit of settings, report data and health page.

Requirements: FR-DGQ-0076, FR-DGQ-0077, FR-DGQ-0078, FR-DGQ-0079
"""
import base64
from datetime import date
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import Form, new_test_user
from odoo.tools import SQL

from ..services import report, retention
from .acceptance_m3_helpers import M3Case


class TestSettingsPurge(M3Case):
    """Requirements: FR-DGQ-0076, FR-DGQ-0077, FR-DGQ-0078, FR-DGQ-0079"""

    def setUp(self):
        super().setUp()
        self.system = new_test_user(
            self.env, login='dgq_t027_system', groups='base.group_user,base.group_system')
        self.system.write({'company_ids': [Command.link(self.cx.id)]})

    def age(self, record, days, column):
        """Age a column after the pending writes of the record are stored (an ORM flush would undo the SQL)."""
        self.env.flush_all()
        self.env.cr.execute(SQL(
            "UPDATE %s SET %s = now() at time zone 'utc' - %s * interval '1 day' WHERE id = %s",
            SQL.identifier(record._table), SQL.identifier(column), days, record.id))
        record.invalidate_recordset([column])

    def closed_issues(self):
        self.isolate('res.partner')
        rule = self.make_rule('res.partner', 'required', ['ref'], severity='warning')
        partners = self.env['res.partner'].create([
            {'name': 'Closed %s' % n, 'company_id': self.cx.id} for n in range(4)])
        return self.env['data.quality.issue'].sudo().browse([self.issue_for(rule, p).id for p in partners])

    def test_cleanup_deletes_old_resolved_and_ignored_issues_only(self):
        """FR-DGQ-0077: a Resolved issue past the retention goes; an Ignored one is dated by its last change."""
        issues = self.closed_issues()
        issues[0].write({'state': 'resolved'})
        issues[1].write({'state': 'ignored', 'ignore_reason': 'not needed'})
        issues[2].write({'state': 'resolved'})
        self.age(issues[0], 366, 'date_resolved')
        self.age(issues[1], 366, 'write_date')
        self.age(issues[2], 364, 'date_resolved')
        self.fire_crons()
        self.assertFalse(issues[0].exists(), 'resolved 366 days ago')
        self.assertFalse(issues[1].exists(), 'ignored 366 days ago')
        self.assertTrue(issues[2].exists(), 'resolved 364 days ago')
        self.assertTrue(issues[3].exists(), 'still open')

    def test_cleanup_clears_the_old_import_file_and_records_the_deletion(self):
        """FR-DGQ-0077: the file of an old import check is emptied and the audit trail says how many."""
        Session = self.env['data.quality.import.session'].sudo()
        values = {'model_id': self.model('res.partner').id, 'company_id': self.cx.id,
                  'file': base64.b64encode(b'name\nA\n').decode(), 'file_name': 'f.csv'}
        old, recent = Session.create([values, values])
        self.age(old, 91, 'create_date')
        self.age(recent, 89, 'create_date')
        self.fire_crons()
        old.invalidate_recordset()
        recent.invalidate_recordset()
        self.assertFalse(old.file)
        self.assertTrue(recent.file)
        entry = self.env['data.quality.audit'].sudo().search([('event', '=', 'cleanup')], limit=1)
        self.assertEqual(entry.changes['import_files'], [False, 1])

    def test_cleanup_without_old_data_writes_no_audit_entry(self):
        """FR-DGQ-0077: nothing deleted, nothing recorded."""
        self.fire_crons()
        self.assertFalse(self.env['data.quality.audit'].sudo().search_count([('event', '=', 'cleanup')]))

    def test_changed_setting_is_written_to_the_audit_trail_with_before_and_after(self):
        """FR-DGQ-0076: a weight change is audited as before and after."""
        with Form(self.env['res.config.settings'].with_user(self.system)) as form:
            form.dgq_weight_warning = 5
        form.record.execute()
        entry = self.env['data.quality.audit'].sudo().search(
            [('event', '=', 'setting_changed'), ('field_names', '=', 'dgq_weight_warning')])
        self.assertEqual(entry.changes, {'dgq_weight_warning': [3, 5]})

    def test_report_counts_issues_by_status_and_averages_resolution_time(self):
        """FR-DGQ-0078: issues by status and the average resolution time of the issues resolved in the dates."""
        issues = self.closed_issues()
        issues[0].write({'state': 'resolved'})
        self.env.flush_all()
        self.env.cr.execute("UPDATE data_quality_issue SET resolution_hours = 10 WHERE id = %s", (issues[0].id,))
        issues[0].invalidate_recordset()
        data = report.report_data(
            self.env(user=self.viewer), [self.cx.id], date(2020, 1, 1), fields.Date.today(), 'month')
        by_status = {row['state']: row['count'] for row in data['issues_by_status']}
        self.assertEqual(by_status, {'Resolved': 1, 'New': 3})
        self.assertEqual(data['average_resolution_hours'], 10.0)

    def test_xlsx_report_is_a_workbook(self):
        """FR-DGQ-0078: the XLSX bytes are a zip workbook."""
        data = report.report_data(
            self.env(user=self.viewer), [self.cx.id], date(2020, 1, 1), fields.Date.today(), 'week')
        self.assertTrue(report.report_xlsx(self.env, data).startswith(b'PK'))

    def test_health_counts_queued_scans_and_lists_failed_jobs_of_other_kinds(self):
        """FR-DGQ-0079: queued scans are counted; a failed duplicate check of the last 7 days is listed."""
        Scan = self.env['data.quality.scan'].sudo()
        Scan.create({'company_ids': [Command.set([self.cx.id])]}).write({'state': 'queued'})
        rule = self.env['data.quality.duplicate.rule'].sudo().create({
            'name': 'Dup', 'model_id': self.model('res.partner').id, 'company_id': self.cx.id,
            'field_line_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get('res.partner', 'name').id, 'method': 'exact'})]})
        check = self.env['data.quality.duplicate.check'].sudo().create({'rule_id': rule.id})
        check.write({'state': 'failed', 'error_message': 'duplicate failure', 'date_end': fields.Datetime.now()})
        health = self.env['data.quality.dashboard'].with_user(self.system).get_health()
        self.assertEqual(health['scans']['queued'], 1)
        self.assertIn(('data.quality.duplicate.check', check.id), [(run['model'], run['id']) for run in health['failed_runs']])

    def test_cleanup_stops_when_the_time_budget_is_spent_and_has_recorded_what_it_deleted(self):
        """FR-DGQ-0077: a call whose time ran out reports work left, and its deletions are already in the audit trail."""
        issues = self.closed_issues()
        issues.write({'state': 'resolved'})
        for issue in issues:
            self.age(issue, 400, 'date_resolved')
        with patch.object(retention, 'JOB_BATCH_SIZE', 1), \
                patch.object(type(self.env['ir.cron']), '_commit_progress', return_value=0) as progress:
            counts = retention.purge(self.env)
        self.assertEqual(counts['issues'], 1, 'one batch, then the call gave way')
        self.assertEqual(progress.call_args.kwargs, {'remaining': 1})
        entry = self.env['data.quality.audit'].sudo().search([('event', '=', 'cleanup')])
        self.assertEqual(entry.changes, {'issues': [False, 1]})
        retention.purge(self.env)
        self.assertFalse(issues.exists())

    def test_health_adds_up_the_cleanup_entries_of_the_last_day(self):
        """FR-DGQ-0079: batches of one cleanup day count together."""
        audit = self.env['data.quality.audit'].sudo()
        audit.create([{'event': 'cleanup', 'changes': {'issues': [False, 2]}} for _ in range(2)])
        health = self.env['data.quality.dashboard'].with_user(self.system).get_health()
        self.assertEqual(health['last_cleanup']['deleted'], {'issues': 4})
