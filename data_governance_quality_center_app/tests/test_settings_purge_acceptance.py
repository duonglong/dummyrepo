"""Acceptance tests of T-027: settings, retention purge, quality report export and health page.

The design names the fields of the settings block, of the report dialog and the models the
health page reads, but not the method of every button. The settings are driven with Odoo's
``Form`` helper on ``res.config.settings`` and ``execute()``; the report button and the health
method are looked up under the usual names (see the helpers) and fail with the list of names.
The daily cleanup is run through the scheduled actions of the app's models, since the design
names no method. What the cleanup records about its deletions has no stated shape, so only
the deletions themselves are asserted.
"""
from datetime import date

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, new_test_user

from .acceptance_m3_helpers import M3Case, call_first, csv_file, dump

REPORT_BUTTONS = ('action_export', 'action_print', 'action_generate', 'action_download',
                  'action_export_report', 'action_report', 'action_confirm')


def values_at(data, word):
    """Every value stored under a key whose name holds ``word``, at any depth."""
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            if word in str(key).lower():
                found.append(value)
            found.extend(values_at(value, word))
    elif isinstance(data, (list, tuple)):
        for item in data:
            found.extend(values_at(item, word))
    return found


class TestSettingsPurgeAcceptance(M3Case):
    """Requirements: FR-DGQ-0076, FR-DGQ-0077, FR-DGQ-0078, FR-DGQ-0079"""

    def setUp(self):
        super().setUp()
        self.system = new_test_user(
            self.env, login='dgq_m4_system', groups='base.group_user,base.group_system')
        self.system.write({'company_ids': [Command.link(self.cx.id)]})

    # ---- helpers -----------------------------------------------------------

    def save_settings(self, **values):
        """The settings screen filled in as an administrator, saved and applied."""
        with Form(self.env['res.config.settings'].with_user(self.system)) as form:
            for name, value in values.items():
                setattr(form, name, value)
        form.record.execute()
        self.env.invalidate_all()

    def age(self, record, days, column='create_date'):
        self.env.flush_all()  # a pending ORM write must not overwrite the SQL date afterwards
        self.env.cr.execute(
            "UPDATE %s SET %s = now() at time zone 'utc' - interval '%s days' WHERE id = %%s"
            % (record._table, column, days), (record.id,))
        record.invalidate_recordset([column])

    def failing_scan(self, rule, partners):
        scan = self.run_scan([rule], models=['res.partner'])
        self.assertEqual(scan.state, 'done')
        self.assertTrue(scan.sudo().line_ids, 'the scan should have result lines')
        return scan

    def contacts_missing_ref(self, count=3):
        self.isolate('res.partner')
        rule = self.make_rule('res.partner', 'required', ['ref'], severity='warning')
        partners = self.env['res.partner'].create([
            {'name': 'Purge %s' % n, 'company_id': self.cx.id} for n in range(count)])
        return rule, partners

    # ---- FR-DGQ-0076 -------------------------------------------------------

    def test_new_weight_is_used_from_the_next_scan_and_old_snapshot_keeps_its_own(self):
        """SC-DGQ-0076-1: warning weight 5; the new snapshot uses 5, the old one still shows 80."""
        self.isolate('res.partner')
        rule = self.make_rule('res.partner', 'required', ['ref'], severity='warning')
        people = self.env['res.partner'].create([
            {'name': 'W %s' % n, 'ref': 'r%s' % n, 'company_id': self.cx.id} for n in range(10)])
        (people[0] | people[1]).write({'ref': False})
        first = self.run_scan([rule], models=['res.partner'])
        Snapshot = self.env['data.quality.score.snapshot'].sudo()
        model = self.model('res.partner')
        old = Snapshot.search([('scan_id', '=', first.id), ('model_id', '=', model.id),
                               ('company_id', '=', self.cx.id), ('rule_id', '=', False)])
        self.assertEqual(round(old.score, 1), 80.0)
        self.save_settings(dgq_weight_warning=5)
        second = self.run_scan([rule], models=['res.partner'])
        new = Snapshot.search([('scan_id', '=', second.id), ('model_id', '=', model.id),
                               ('company_id', '=', self.cx.id), ('rule_id', '=', False)])
        self.assertEqual((new.applicable_weight, new.failed_weight), (50, 10), 'weight 5 per warning')
        self.assertEqual(new.weights_used.get('warning'), 5)
        old.invalidate_recordset()
        self.assertEqual(round(old.score, 1), 80.0, 'the old snapshot keeps its score')
        self.assertEqual((old.applicable_weight, old.failed_weight), (30, 6))
        self.assertEqual(old.weights_used.get('warning'), 3, 'and the weights it was computed with')

    def test_other_settings_are_kept_for_the_next_use(self):
        """FR-DGQ-0076: scan frequency, escalation delay, notification policy and retention are saved."""
        Settings = self.env['res.config.settings'].with_user(self.system)
        frequency = [key for key, _label in Settings._fields['dgq_scan_frequency']._description_selection(self.env)]
        chosen = frequency[-1]
        self.save_settings(
            dgq_scan_frequency=chosen, dgq_escalation_days=9, dgq_notify_email=False,
            dgq_scan_line_retention_days=45, dgq_issue_retention_days=200)
        saved = Settings.default_get([
            'dgq_scan_frequency', 'dgq_escalation_days', 'dgq_notify_email',
            'dgq_scan_line_retention_days', 'dgq_issue_retention_days', 'dgq_weight_warning'])
        self.assertEqual(saved['dgq_scan_frequency'], chosen)
        self.assertEqual(saved['dgq_escalation_days'], 9)
        self.assertFalse(saved['dgq_notify_email'])
        self.assertEqual(saved['dgq_scan_line_retention_days'], 45)
        self.assertEqual(saved['dgq_issue_retention_days'], 200)
        self.assertEqual(saved['dgq_weight_warning'], 3, 'the default warning weight is 3')

    def test_warning_weight_above_critical_weight_is_refused(self):
        """SC-DGQ-0076-2: warning 12 and critical 10 is refused, saying warning cannot exceed critical."""
        with self.assertRaises((UserError, ValidationError)) as caught:
            self.save_settings(dgq_weight_warning=12, dgq_weight_critical=10)
        message = str(caught.exception).lower()
        self.assertIn('warning', message)
        self.assertIn('critical', message)
        self.assertIn('exceed', message)

    def test_weights_at_the_limits_are_allowed(self):
        """FR-DGQ-0076 boundary: info = warning = critical is allowed (info <= warning <= critical)."""
        self.save_settings(dgq_weight_info=10, dgq_weight_warning=10, dgq_weight_critical=10)
        saved = self.env['res.config.settings'].with_user(self.system).default_get(
            ['dgq_weight_info', 'dgq_weight_warning', 'dgq_weight_critical'])
        self.assertEqual(
            (saved['dgq_weight_info'], saved['dgq_weight_warning'], saved['dgq_weight_critical']), (10, 10, 10))

    def test_weights_just_above_the_order_or_not_positive_are_refused(self):
        """FR-DGQ-0076 boundary: info 4 above warning 3 is refused; a weight of 0 is refused."""
        with self.assertRaises((UserError, ValidationError)):
            self.save_settings(dgq_weight_info=4, dgq_weight_warning=3, dgq_weight_critical=10)
        with self.assertRaises((UserError, ValidationError)):
            self.save_settings(dgq_weight_info=0, dgq_weight_warning=3, dgq_weight_critical=10)

    # ---- FR-DGQ-0077 -------------------------------------------------------

    def test_cleanup_deletes_old_scan_lines_and_keeps_recent_ones_and_totals(self):
        """SC-DGQ-0077-1: retention 90 days; lines of 120 days ago go, of 30 days ago stay, totals stay."""
        self.save_settings(dgq_scan_line_retention_days=90)
        rule, _partners = self.contacts_missing_ref(3)
        old_scan = self.failing_scan(rule, _partners)
        recent_scan = self.failing_scan(rule, _partners)
        totals = (old_scan.records_scanned, old_scan.checks_failed)
        self.assertTrue(totals[0] and totals[1])
        for line in old_scan.sudo().line_ids:
            self.age(line, 120)
        for line in recent_scan.sudo().line_ids:
            self.age(line, 30)
        self.age(old_scan, 120)
        self.fire_crons()
        Line = self.env['data.quality.scan.line'].sudo()
        self.assertFalse(Line.search([('scan_id', '=', old_scan.id)]), 'lines of 120 days ago are deleted')
        self.assertTrue(Line.search([('scan_id', '=', recent_scan.id)]), 'lines of 30 days ago remain')
        old_scan.invalidate_recordset()
        self.assertTrue(old_scan.exists(), 'the scan itself stays')
        self.assertEqual((old_scan.records_scanned, old_scan.checks_failed), totals, 'and keeps its totals')

    def test_cleanup_keeps_a_line_at_the_retention_age_and_deletes_one_day_older(self):
        """FR-DGQ-0077 boundary: retention 90 days: a line 89 days old stays, one 91 days old goes."""
        self.save_settings(dgq_scan_line_retention_days=90)
        rule, partners = self.contacts_missing_ref(2)
        scan = self.failing_scan(rule, partners)
        lines = scan.sudo().line_ids.sorted('id')
        self.assertEqual(len(lines), 2)
        self.age(lines[0], 91)
        self.age(lines[1], 89)
        keep = lines[1].id
        self.fire_crons()
        left = self.env['data.quality.scan.line'].sudo().search([('scan_id', '=', scan.id)])
        self.assertEqual(left.ids, [keep])

    def test_cleanup_deletes_old_closed_issues_and_keeps_recent_and_open_ones(self):
        """FR-DGQ-0077: Resolved issues closed longer than the issue retention (365 days) are deleted."""
        self.save_settings(dgq_issue_retention_days=365)
        rule, partners = self.contacts_missing_ref(3)
        issues = self.env['data.quality.issue'].sudo().browse([
            self.issue_for(rule, partner).id for partner in partners])
        issues.write({'state': 'resolved'})
        self.age(issues[0], 400, 'date_resolved')
        self.age(issues[1], 10, 'date_resolved')
        issues[2].write({'state': 'new'})
        self.fire_crons()
        self.assertFalse(issues[0].exists(), 'resolved 400 days ago: deleted')
        self.assertTrue(issues[1].exists(), 'resolved 10 days ago: kept')
        self.assertTrue(issues[2].exists(), 'an open issue is never purged')

    def test_cleanup_keeps_the_score_snapshots(self):
        """FR-DGQ-0077: snapshots stay when the scan lines are purged, so the trend is unchanged."""
        rule, partners = self.contacts_missing_ref(2)
        scan = self.failing_scan(rule, partners)
        Snapshot = self.env['data.quality.score.snapshot'].sudo()
        before = Snapshot.search([('scan_id', '=', scan.id)]).ids
        self.assertTrue(before)
        for line in scan.sudo().line_ids:
            self.age(line, 500)
        self.age(scan, 500)
        self.fire_crons()
        self.assertFalse(self.env['data.quality.scan.line'].sudo().search([('scan_id', '=', scan.id)]),
                         'the old lines are purged')
        self.assertEqual(Snapshot.search([('scan_id', '=', scan.id)]).ids, before)

    def test_cleanup_deletes_the_file_of_an_old_import_check(self):
        """FR-DGQ-0077: the import file of a check older than the scan detail retention is deleted."""
        self.save_settings(dgq_scan_line_retention_days=90)
        old = self.make_session(csv_file(['name'], [['A'], ['B']]), 'old.csv')
        recent = self.make_session(csv_file(['name'], [['C']]), 'recent.csv')
        self.age(old, 120)
        self.age(recent, 30)
        self.fire_crons()
        old.invalidate_recordset()
        recent.invalidate_recordset()
        self.assertFalse(old.exists() and old.sudo().file, 'the old file is deleted')
        self.assertTrue(recent.sudo().file, 'the recent file stays')

    # ---- FR-DGQ-0078 -------------------------------------------------------

    def quality_figures(self):
        """Company X: score 85, 12 open issues, a model row and a rule row, as the dashboard shows."""
        rule = self.make_rule('res.partner', 'required', ['ref'], severity='warning', name='Contacts need a reference')
        partners = self.env['res.partner'].create([
            {'name': 'Open %s' % n, 'company_id': self.cx.id} for n in range(12)])
        issues = self.env['data.quality.issue'].sudo().browse([
            self.issue_for(rule, partner).id for partner in partners])
        self.assertEqual(len(issues), 12)
        Snapshot = self.env['data.quality.score.snapshot'].sudo()
        common = {'date': fields.Datetime.now(), 'company_id': self.cx.id,
                  'model_id': self.model('res.partner').id, 'severity': 'warning',
                  'applicable_weight': 100, 'failed_weight': 15, 'score': 85.0, 'has_score': True,
                  'weights_used': {'info': 1, 'warning': 3, 'critical': 10}, 'records_scanned': 40,
                  'issues_open_warning': 12}
        Snapshot.create([common, dict(common, rule_id=rule.id, rule_version=rule.version)])
        return rule

    def open_report(self, file_format, user=None):
        Wizard = self.env['data.quality.report.wizard'].with_user(user or self.viewer)
        with Form(Wizard) as form:
            form.company_ids.add(self.cx)
            form.date_from = date(2020, 1, 1)
            form.date_to = fields.Date.today()
            form.file_format = file_format
        return call_first(form.record, REPORT_BUTTONS)

    def test_pdf_report_shows_score_open_issues_and_scores_by_model_and_rule(self):
        """SC-DGQ-0078-1: a viewer's PDF report of company A shows score 85, 12 open issues, model and rule."""
        rule = self.quality_figures()
        action = self.open_report('pdf')
        self.assertEqual(action.get('type'), 'ir.actions.report', 'the PDF format returns a report action')
        wizard_ids = action.get('context', {}).get('active_ids') or action.get('res_ids') or []
        html = self.env['ir.actions.report'].with_user(self.viewer)._render_qweb_html(
            action['report_name'], wizard_ids, action.get('data'))[0]
        text = html.decode() if isinstance(html, bytes) else html
        self.assertIn('85', text)
        self.assertIn('12', text)
        self.assertIn(self.model('res.partner').name, text, 'the score by model')
        self.assertIn(rule.name, text, 'the score by rule')

    def test_xlsx_report_is_returned_as_a_download(self):
        """FR-DGQ-0078: the XLSX format returns a file to download (the audit export's way: a URL action)."""
        self.quality_figures()
        action = self.open_report('xlsx')
        self.assertEqual(action.get('type'), 'ir.actions.act_url')
        self.assertTrue(action.get('url'))

    # ---- FR-DGQ-0079 -------------------------------------------------------

    def health(self, user=None):
        dashboard = self.env['data.quality.dashboard'].with_user(user or self.system)
        names = sorted(name for name in dir(dashboard) if 'health' in name and not name.startswith('__'))
        public = [name for name in names if not name.startswith('_')]
        self.assertTrue(public, 'data.quality.dashboard has no health method')
        return getattr(dashboard, public[0])()

    def test_health_page_shows_one_running_scan_and_the_failed_one_with_reason_and_link(self):
        """SC-DGQ-0079-1: 1 running scan; the scan failed yesterday shows its reason and its id to link."""
        Scan = self.env['data.quality.scan'].sudo()
        running = Scan.create({'company_ids': [Command.set([self.cx.id])]})
        running.write({'state': 'running', 'date_start': fields.Datetime.now()})
        failed = Scan.create({'company_ids': [Command.set([self.cx.id])]})
        failed.write({'state': 'failed', 'error_message': 'database error: deadlock detected',
                      'date_end': fields.Datetime.subtract(fields.Datetime.now(), days=1)})
        data = self.health()
        counts = [value if not isinstance(value, (list, tuple)) else len(value)
                  for value in values_at(data, 'running')]
        self.assertIn(1, counts, 'one running scan; got %s' % dump(data))
        text = dump(data)
        self.assertIn('database error: deadlock detected', text, 'the failure reason')
        self.assertIn(str(failed.id), text, 'the id of the failed scan, to open its record')

    def test_health_page_leaves_out_failures_older_than_seven_days(self):
        """FR-DGQ-0079: failed runs of the last 7 days are listed; one of 10 days ago is not."""
        Scan = self.env['data.quality.scan'].sudo()
        old = Scan.create({'company_ids': [Command.set([self.cx.id])]})
        old.write({'state': 'failed', 'error_message': 'ancient failure xyz',
                   'date_end': fields.Datetime.subtract(fields.Datetime.now(), days=10)})
        recent = Scan.create({'company_ids': [Command.set([self.cx.id])]})
        recent.write({'state': 'failed', 'error_message': 'recent failure abc',
                      'date_end': fields.Datetime.subtract(fields.Datetime.now(), days=6)})
        text = dump(self.health())
        self.assertIn('recent failure abc', text)
        self.assertNotIn('ancient failure xyz', text)

    def test_health_page_is_for_administrators_only(self):
        """FR-DGQ-0079: a viewer is refused the health page."""
        with self.assertRaises(AccessError):
            self.health(user=self.viewer)
