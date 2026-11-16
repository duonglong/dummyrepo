from odoo.tests import tagged

from odoo.addons.data_governance_quality_center_app.services import report

from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestReportTrend(M3Case):
    """Regression test for B-0221: the report's trend reads model rows only.

    The report is read through the service the export routes call; the PDF and spreadsheet
    layouts are not reached.

    Requirements: FR-DGQ-0078, FR-DGQ-0034, FR-DGQ-0031
    """

    def test_b_0221_the_trend_ignores_the_snapshot_rows_of_a_deleted_rule(self):
        """B-0221: a rule row whose rule was deleted does not replace the score of its model."""
        Snapshot = self.env['data.quality.score.snapshot']
        Snapshot.search([]).unlink()
        rule = self.vendor_rule(severity='critical')
        values = {'model_id': self.model('res.partner').id, 'company_id': self.cx.id, 'date': '2026-11-10 08:00:00'}
        Snapshot.create(dict(values, applicable_weight=100, failed_weight=40, has_score=True))
        Snapshot.create(dict(
            values, applicable_weight=10, failed_weight=0, has_score=True, rule_id=rule.id, rule_version=1,
            severity=rule.severity))
        rule.unlink()
        data = report.report_data(self.env(user=self.administrator), [self.cx.id], None, None, 'week')
        self.assertEqual([point['score'] for point in data['trend']], [60.0])
