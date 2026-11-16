from odoo import fields
from odoo.tests import tagged

from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestDashboardFiguresAcceptance(M3Case):
    """Acceptance tests of the dashboard figures.

    Requirements: FR-DGQ-0032
    The dashboard form step belongs to the client widget; the server entry
    ``data.quality.dashboard.get_figures`` is tested, as a viewer of company A.
    """

    def snap(self, model_name, applicable, failed, **vals):
        return self.env['data.quality.score.snapshot'].create(dict({
            'model_id': self.model(model_name).id, 'company_id': self.cx.id,
            'date': fields.Datetime.now(), 'applicable_weight': applicable,
            'failed_weight': failed, 'has_score': bool(applicable), 'records_scanned': 10}, **vals))

    def figures(self, user):
        return self.env['data.quality.dashboard'].with_user(user).get_figures()

    def test_figures_from_the_latest_snapshots(self):
        """SC-DGQ-0032-1: Contact 80, Product 95, 12 open issues of which 3 critical, Contact lowest first."""
        self.env['data.quality.score.snapshot'].search([]).unlink()
        self.snap('res.partner', 100, 20, issues_open_critical=3, issues_open_warning=9)
        self.snap('product.template', 100, 5)
        data = self.figures(self.viewer)
        self.assertTrue(data['has_scan'])
        scores = {row['name']: row['score'] for row in data['by_model']}
        self.assertEqual(sorted(scores.values()), [80.0, 95.0])
        self.assertEqual(data['score'], 87.5)
        self.assertEqual(data['open_issues'], 12)
        self.assertEqual(data['critical_issues'], 3)
        self.assertEqual(data['issues_by_severity']['critical'], 3)
        self.assertEqual(data['lowest_models'][0]['score'], 80.0)
        self.assertEqual(data['lowest_models'][0]['name'], self.model('res.partner').name)
        self.assertEqual([row['score'] for row in data['by_company']], [87.5])

    def test_no_scan_yet(self):
        """SC-DGQ-0032-2: no scan has run: the dashboard says so and offers a scan to operators and above only."""
        self.env['data.quality.score.snapshot'].search([]).unlink()
        viewer_data = self.figures(self.viewer)
        self.assertFalse(viewer_data['has_scan'])
        self.assertFalse(viewer_data['can_start_scan'])
        self.assertTrue(self.figures(self.operator)['can_start_scan'])
        self.assertTrue(self.figures(self.manager_a)['can_start_scan'])
