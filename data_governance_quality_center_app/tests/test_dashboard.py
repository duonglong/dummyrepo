from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestDashboard(M3Case):
    """Dashboard figures read from the snapshots, filtered by company and date.

    Requirements: FR-DGQ-0032, FR-DGQ-0033, NFR-DGQ-0004
    """

    def snap(self, model_name, company, applicable, failed, date, **vals):
        return self.env['data.quality.score.snapshot'].create(dict({
            'model_id': self.model(model_name).id, 'company_id': company.id, 'date': date,
            'applicable_weight': applicable, 'failed_weight': failed, 'has_score': bool(applicable),
            'records_scanned': 10}, **vals))

    def figures(self, user=None, **kw):
        return self.env['data.quality.dashboard'].with_user(user or self.viewer).get_figures(**kw)

    def test_no_scan_yet(self):
        """SC-DGQ-0032-2: no snapshot says no scan has run, and offers one to operators only."""
        self.env['data.quality.score.snapshot'].search([]).unlink()
        self.assertFalse(self.figures()['has_scan'])
        self.assertFalse(self.figures()['can_start_scan'])
        self.assertTrue(self.figures(self.operator)['can_start_scan'])

    def test_scores_by_model_and_lowest_first(self):
        """SC-DGQ-0032-1: scores by model and the lowest-scoring model first."""
        self.env['data.quality.score.snapshot'].search([]).unlink()
        now = fields.Datetime.now()
        self.snap('res.partner', self.cx, 100, 20, now, issues_open_critical=3, issues_open_warning=9)
        self.snap('product.template', self.cx, 100, 5, now)
        data = self.figures()
        scores = {row['name']: row['score'] for row in data['by_model']}
        self.assertEqual(sorted(scores.values()), [80.0, 95.0])
        self.assertEqual(data['lowest_models'][0]['score'], 80.0)
        self.assertEqual((data['open_issues'], data['critical_issues']), (12, 3))
        self.assertEqual(data['score'], 87.5)

    def test_date_filter_shows_the_last_state_at_that_date(self):
        """SC-DGQ-0033-1: the snapshot of 2026-10-31 gives 70, the later one 85 is not used."""
        self.env['data.quality.score.snapshot'].search([]).unlink()
        self.snap('res.partner', self.cx, 100, 30, '2026-10-31 10:00:00')
        self.snap('res.partner', self.cx, 100, 15, '2026-11-12 10:00:00')
        self.assertEqual(self.figures(date='2026-10-31')['score'], 70.0)
        self.assertEqual(self.figures()['score'], 85.0)

    def test_only_allowed_companies(self):
        """SC-DGQ-0033-2: only the user's companies are offered and used."""
        self.env['data.quality.score.snapshot'].search([]).unlink()
        now = fields.Datetime.now()
        self.snap('res.partner', self.cx, 100, 30, now)
        self.snap('res.partner', self.cy, 100, 0, now)
        viewer = self.viewer
        viewer.write({'company_ids': [Command.set(self.cx.ids)], 'company_id': self.cx.id})
        data = self.figures(viewer, company_ids=[self.cx.id, self.cy.id])
        self.assertEqual([c['id'] for c in data['companies']], self.cx.ids)
        self.assertEqual(data['score'], 70.0)

    def test_a_user_without_viewer_access_is_refused(self):
        """FR-DGQ-0032: only viewers and above open the dashboard."""
        outsider = self.env['res.users'].create({'name': 'Out', 'login': 'dgq_out', 'group_ids': [Command.link(self.env.ref('base.group_user').id)]})
        with self.assertRaises(AccessError):
            self.figures(outsider)

    def test_only_active_companies_are_offered_and_used(self):
        """FR-DGQ-0033: a company allowed but not ticked in the switcher is neither offered nor used."""
        self.env['data.quality.score.snapshot'].search([]).unlink()
        now = fields.Datetime.now()
        self.snap('res.partner', self.cx, 100, 30, now)
        self.snap('res.partner', self.cy, 100, 0, now)
        viewer = self.viewer
        viewer.write({'company_ids': [Command.set((self.cx | self.cy).ids)], 'company_id': self.cx.id})
        dashboard = self.env['data.quality.dashboard'].with_user(viewer).with_context(allowed_company_ids=self.cx.ids)
        data = dashboard.get_figures(company_ids=[self.cx.id, self.cy.id])
        self.assertEqual([c['id'] for c in data['companies']], self.cx.ids)
        self.assertEqual(data['score'], 70.0)

    def test_issues_by_severity(self):
        """FR-DGQ-0032: the figures carry the open issues of every severity."""
        self.env['data.quality.score.snapshot'].search([]).unlink()
        self.snap('res.partner', self.cx, 100, 20, fields.Datetime.now(),
                  issues_open_info=2, issues_open_warning=9, issues_open_critical=3)
        self.assertEqual(self.figures()['issues_by_severity'], {'info': 2, 'warning': 9, 'critical': 3})

    def test_the_day_ends_in_the_user_time_zone(self):
        """FR-DGQ-0033: at UTC+10, a snapshot taken at 2026-10-31 15:00 UTC is already 2026-11-01 locally."""
        self.env['data.quality.score.snapshot'].search([]).unlink()
        self.snap('res.partner', self.cx, 100, 30, '2026-10-31 10:00:00')
        self.snap('res.partner', self.cx, 100, 15, '2026-10-31 15:00:00')
        self.viewer.tz = 'Australia/Brisbane'
        self.assertEqual(self.figures(date='2026-10-31')['score'], 70.0)
        self.assertEqual(self.figures(date='2026-11-01')['score'], 85.0)

    def test_a_malformed_date_is_refused_with_a_message(self):
        """FR-DGQ-0033: a bad date from the client is a user error, not a traceback."""
        with self.assertRaises(UserError):
            self.figures(date='not a date')
