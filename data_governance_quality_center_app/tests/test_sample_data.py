import io
import zipfile

from odoo import fields
from odoo.tests import tagged

from ..services import report, scoring
from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestSampleData(M3Case):
    """Requirements: FR-DGQ-0080"""

    def test_sample_duplicate_rules_are_switched_off(self):
        """Requirements: FR-DGQ-0080 - the duplicate templates do not run by themselves."""
        rules = self.env['data.quality.duplicate.rule'].sudo().with_context(active_test=False).search([  # sudo: test set-up
            ('model_name', 'in', ('res.partner', 'product.template'))])
        self.assertTrue(rules)
        self.assertFalse(rules.filtered('active'))

    def test_sample_rules_have_a_version_and_check_their_model(self):
        """Requirements: FR-DGQ-0080 - a sample rule is a complete rule once switched on."""
        rule = self.env.ref('data_governance_quality_center_app.sample_rule_product_price')
        rule.active = True
        self.assertEqual(rule.version, 1)
        self.assertEqual(rule.model_name, 'product.template')

    def test_scan_is_illustrative_flag_defaults_off(self):
        """Requirements: FR-DGQ-0080 - a live scan is never labelled illustrative."""
        scan = self.make_scan()
        self.assertFalse(scan.is_illustrative)

    def _issue(self, rule, partner, **vals):
        return self.env['data.quality.issue'].sudo().create(dict({  # sudo: test set-up
            'rule_id': rule.id, 'rule_version': 1, 'res_model': 'res.partner', 'res_id': partner.id,
            'record_name': partner.name, 'severity': 'warning', 'company_id': self.cx.id}, **vals))

    def test_live_issue_counts_leave_out_illustrative_issues(self):
        """Requirements: FR-DGQ-0080 - a live figure counts live results only, never a demo issue."""
        rule = self.env.ref('data_governance_quality_center_app.sample_rule_contact_email')
        partners = self.env['res.partner'].create([{'name': 'Live P'}, {'name': 'Demo P'}])
        self._issue(rule, partners[0])
        self._issue(rule, partners[1], is_illustrative=True)
        counts = scoring.open_issue_counts(
            self.env, self.env['ir.model'].sudo().search([('model', '=', 'res.partner')]))  # sudo: test set-up
        self.assertEqual(counts[('res.partner', self.cx.id)]['warning'], 1)

    def test_recent_issues_say_which_are_illustrative(self):
        """Requirements: FR-DGQ-0080 - each latest issue row of the dashboard carries its own illustrative flag."""
        rule = self.env.ref('data_governance_quality_center_app.sample_rule_contact_email')
        partner = self.env['res.partner'].create({'name': 'Demo Q'})
        issue = self._issue(rule, partner, is_illustrative=True)
        rows = self.env['data.quality.dashboard'].sudo()._recent_issues(self.cx, None)  # sudo: test set-up
        self.assertTrue(next(row for row in rows if row['id'] == issue.id)['is_illustrative'])

    # ---- a live scan takes the demo flag off what it finds (R2-Q1) ---------

    def _required_email_rule(self):
        self.isolate('res.partner')
        return self.make_rule('res.partner', 'required', ['email'])

    def test_live_scan_takes_the_demo_flag_off_the_issue_it_refreshes(self):
        """Requirements: FR-DGQ-0080, FR-DGQ-0022 - a demo issue a live scan finds again is a live finding.

        The live snapshot counts it, and the issue no longer says illustrative.
        """
        rule = self._required_email_rule()
        partner = self.partner('Demo contact found live')
        issue = self._issue(rule, partner, is_illustrative=True)
        scan = self.run_scan(rules=[rule])
        self.assertEqual(self.issues_of(rule), issue, 'the scan refreshes the issue, it raises no second one')
        self.assertFalse(issue.is_illustrative)
        snapshot = self.env['data.quality.score.snapshot'].sudo().search([  # sudo: test set-up
            ('scan_id', '=', scan.id), ('company_id', '=', self.cx.id), ('rule_id', '=', False)])
        self.assertEqual(snapshot.records_failing, 1)
        self.assertEqual(snapshot.issues_open_warning, 1, 'the live figure counts the failure the scan found')

    def test_live_scan_takes_the_demo_flag_off_the_issue_it_reopens(self):
        """Requirements: FR-DGQ-0080, FR-DGQ-0023 - a resolved demo issue reopened by a live scan is live."""
        rule = self._required_email_rule()
        partner = self.partner('Demo contact reopened live')
        issue = self._issue(rule, partner, is_illustrative=True, state='resolved')
        self.run_scan(rules=[rule])
        self.assertEqual(issue.state, 'new')
        self.assertFalse(issue.is_illustrative)

    def test_live_scan_takes_the_demo_flag_off_the_issue_it_resolves(self):
        """Requirements: FR-DGQ-0080, FR-DGQ-0023 - a demo issue closed by a live scan is closed by live data."""
        rule = self._required_email_rule()
        partner = self.partner('Demo contact fixed', email='fixed@example.com')
        issue = self._issue(rule, partner, is_illustrative=True)
        self.run_scan(rules=[rule])
        self.assertEqual(issue.state, 'resolved')
        self.assertFalse(issue.is_illustrative)

    # ---- every output of a figure says when it is illustrative (R2-Q1, R2-Q2)

    def _snapshot(self, illustrative, date=None):
        return self.env['data.quality.score.snapshot'].sudo().create({  # sudo: test set-up
            'date': date or fields.Datetime.now(), 'company_id': self.cx.id,
            'model_id': self.model('res.partner').id, 'applicable_weight': 10, 'failed_weight': 1,
            'score': 90.0, 'has_score': True, 'issues_open_warning': 1,
            'weights_used': {'info': 1, 'warning': 3, 'critical': 10}, 'is_illustrative': illustrative})

    def _drill_ids(self):
        action = self.env['data.quality.dashboard'].with_user(self.viewer).with_context(
            allowed_company_ids=self.cx.ids).drill_down('open_issues', company_ids=[self.cx.id])
        return self.env['data.quality.issue'].sudo().search(action['domain']).ids  # sudo: test set-up

    def test_drill_down_follows_the_figure_it_opens(self):
        """Requirements: FR-DGQ-0036, FR-DGQ-0080 - the list behind a live figure holds live issues only.

        While the figure comes from a demo snapshot it lists the demo issue it counts.
        """
        rule = self.env.ref('data_governance_quality_center_app.sample_rule_contact_email')
        issue = self._issue(rule, self.partner('Demo drill'), is_illustrative=True)
        self._snapshot(True, date=fields.Datetime.subtract(fields.Datetime.now(), hours=1))
        self.assertIn(issue.id, self._drill_ids())
        self._snapshot(False)
        self.assertNotIn(issue.id, self._drill_ids())

    def _report(self):
        wizard = self.env['data.quality.report.wizard'].with_user(self.viewer).with_context(
            allowed_company_ids=self.cx.ids).create({'company_ids': [(6, 0, self.cx.ids)]})
        return wizard, wizard._report_data()

    def test_quality_report_says_its_figures_are_illustrative(self):
        """Requirements: FR-DGQ-0078, FR-DGQ-0080 - a report built from demo snapshots is labelled in XLSX and PDF."""
        self._snapshot(True)
        wizard, data = self._report()
        self.assertTrue(data['is_illustrative'])
        with zipfile.ZipFile(io.BytesIO(report.report_xlsx(self.env, data))) as book:
            self.assertIn('Illustrative', book.read('xl/sharedStrings.xml').decode())
        html, _kind = self.env['ir.actions.report'].sudo()._render_qweb_html(  # sudo: test set-up
            'data_governance_quality_center_app.report_quality', wizard.ids)
        self.assertIn('Illustrative', html.decode())

    def test_quality_report_from_live_figures_is_not_labelled(self):
        """Requirements: FR-DGQ-0078, FR-DGQ-0080 - a report of live snapshots and issues carries no label."""
        self._snapshot(False)
        _wizard, data = self._report()
        self.assertFalse(data['is_illustrative'])
        with zipfile.ZipFile(io.BytesIO(report.report_xlsx(self.env, data))) as book:
            self.assertNotIn('Illustrative', book.read('xl/sharedStrings.xml').decode())

    def test_quality_report_counting_a_demo_issue_is_labelled(self):
        """Requirements: FR-DGQ-0078, FR-DGQ-0080 - the issue statuses of a report count a demo issue, so it says so."""
        self._snapshot(False)
        rule = self.env.ref('data_governance_quality_center_app.sample_rule_contact_email')
        self._issue(rule, self.partner('Demo in report'), is_illustrative=True)
        _wizard, data = self._report()
        self.assertTrue(data['is_illustrative'])

    def test_trend_marks_each_point_built_from_a_demo_snapshot(self):
        """Requirements: FR-DGQ-0034, FR-DGQ-0080 - a trend point from a demo snapshot says illustrative, a live one not."""
        self._snapshot(True, date=fields.Datetime.subtract(fields.Datetime.now(), days=3))
        live = self._snapshot(False)
        dashboard = self.env['data.quality.dashboard'].with_user(self.viewer).with_context(
            allowed_company_ids=self.cx.ids)
        points = [point for point in dashboard.get_trend(company_ids=[self.cx.id], period='day')['points']
                  if point['score'] is not None]
        self.assertEqual([point['is_illustrative'] for point in points], [True, False])
        explanation = dashboard.explain_change(live.id)
        self.assertTrue(explanation['is_illustrative'], 'a change measured against a demo snapshot says so')
