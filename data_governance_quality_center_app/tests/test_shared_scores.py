from odoo.tests import tagged

from odoo.addons.data_governance_quality_center_app.services import report

from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestSharedScores(M3Case):
    """Regression tests for B-0210: records with no company are scored apart and every company sees them.

    A scan of any companies stores the score of the contacts with no company under no company, and
    the dashboard, the trend, the explanation of a change and the quality report of every viewer
    add it to their own figures. The screens are client side; the calls they make are used here.

    Requirements: FR-DGQ-0031, FR-DGQ-0032, FR-DGQ-0071
    """

    def setUp(self):
        super().setUp()
        self.isolate('res.partner')
        self.viewer_x = self.user('viewer', login='dgq_shared_viewer_x', company=self.cx)
        self.viewer_y = self.user('viewer', login='dgq_shared_viewer_y', company=self.cy)
        self.rule = self.vendor_rule(severity='critical', company=False)
        self.shared = self.env['res.partner'].create({
            'name': 'Shared vendor', 'function': 'DGQVEND', 'company_id': False})

    def _scan(self, *companies):
        return self.run_scan([self.rule], companies=list(companies), models=['res.partner'])

    def _dashboard(self, user, **kw):
        return self.env['data.quality.dashboard'].with_user(user).get_figures(**kw)

    def test_b_0210_the_score_of_a_company_less_record_is_stored_with_no_company(self):
        """B-0210: a scan of two companies stores the shared contacts under no company, not the first one."""
        scan = self._scan(self.cx, self.cy)
        Snapshot = self.env['data.quality.score.snapshot']
        shared = Snapshot.search([('scan_id', '=', scan.id), ('rule_id', '=', False), ('company_id', '=', False)])
        self.assertEqual((shared.applicable_weight, shared.failed_weight), (10, 10))
        self.assertEqual(shared.issues_open_critical, 1)
        first = Snapshot.search([('scan_id', '=', scan.id), ('rule_id', '=', False), ('company_id', '=', self.cx.id)])
        self.assertEqual(first.applicable_weight, 0, "the shared contacts were scored under the first company")

    def test_b_0210_a_viewer_of_a_company_the_scan_did_not_cover_sees_the_shared_score(self):
        """B-0210: scan of company X only; the viewer of Y sees the shared contact's score and issue."""
        self._scan(self.cx)
        figures = self._dashboard(self.viewer_y)
        self.assertEqual(figures['score'], 0.0)
        self.assertEqual(figures['critical_issues'], 1)
        self.assertEqual([row['score'] for row in figures['by_model']], [0.0])

    def test_b_0210_every_viewer_sees_the_same_shared_figures(self):
        """B-0210: the figure does not depend on the companies a scan covered: Y-only scan, X viewer."""
        self._scan(self.cy)
        shared = self._dashboard(self.viewer_x)
        self.assertEqual((shared['score'], shared['critical_issues']), (0.0, 1))
        self.assertEqual(self._dashboard(self.viewer_y)['score'], 0.0)

    def test_b_0210_the_shared_score_has_a_row_of_its_own_by_company(self):
        """B-0210: the scores by company list the shared records apart, and no company holds them."""
        self._scan(self.cx, self.cy)
        by_company = self._dashboard(self.viewer_y)['by_company']
        scored = {row['name']: row['score'] for row in by_company if row['score'] is not None}
        self.assertEqual(list(scored.values()), [0.0])
        self.assertNotIn(self.cx.name, scored)

    def test_b_0210_the_trend_of_a_viewer_includes_the_shared_snapshots(self):
        """B-0210: the trend points of the viewer of Y carry the score of the shared contacts."""
        self._scan(self.cx)
        points = self.env['data.quality.dashboard'].with_user(self.viewer_y).get_trend()['points']
        self.assertEqual([point['score'] for point in points], [0.0])

    def test_b_0210_the_explanation_of_a_change_reads_the_shared_snapshots(self):
        """B-0210: two scans of the shared contacts can be compared by a viewer of any company."""
        self._scan(self.cx)
        self.env['res.partner'].browse(self.shared.id).write({'ref': 'FIXED'})
        self._scan(self.cx)
        Snapshot = self.env['data.quality.score.snapshot']
        latest = Snapshot.search([('company_id', '=', False), ('rule_id', '=', False)], order='id desc', limit=1)
        explained = self.env['data.quality.dashboard'].with_user(self.viewer_y).explain_change(latest.id)
        self.assertEqual((explained['score_before'], explained['score_after']), (0.0, 100.0))

    def test_b_0210_the_quality_report_of_a_viewer_includes_the_shared_rows(self):
        """B-0210: the report's scores by rule and by company hold the shared contacts."""
        self._scan(self.cx)
        data = report.report_data(self.env(user=self.viewer_y), [self.cy.id], None, None, 'week')
        self.assertEqual([row['score'] for row in data['by_rule'] if row['name'] == self.rule.name], [0.0])
        self.assertEqual(data['score'], 0.0)
