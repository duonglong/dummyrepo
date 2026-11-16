from odoo.tests import tagged

from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestTrendAcceptance(M3Case):
    """Acceptance tests of the score trend.

    Requirements: FR-DGQ-0034
    The trend widget belongs to the client; the server entry
    ``data.quality.dashboard.get_trend(period, model_id)`` is tested.
    """

    def setUp(self):
        super().setUp()
        self.env['data.quality.score.snapshot'].search([]).unlink()
        self.contact = self.model('res.partner')

    def snap(self, failed, date, model=None):
        return self.env['data.quality.score.snapshot'].create({
            'model_id': (model or self.contact).id, 'company_id': self.cx.id, 'date': date,
            'applicable_weight': 100, 'failed_weight': failed, 'has_score': True, 'records_scanned': 10})

    def trend(self, **kw):
        return self.env['data.quality.dashboard'].with_user(self.viewer).get_trend(**kw)['points']

    def test_weekly_trend_shows_a_gap_for_the_week_without_a_scan(self):
        """SC-DGQ-0034-1: Contact scored 70 in week 44, 82 in week 46, no scan in week 45."""
        self.snap(30, '2026-10-28 10:00:00')  # week 44
        self.snap(18, '2026-11-11 10:00:00')  # week 46
        points = self.trend(period='week', model_id=self.contact.id)
        self.assertEqual([p['period'] for p in points], ['2026-10-26', '2026-11-02', '2026-11-09'])
        self.assertEqual([None if p['score'] is None else round(p['score'], 1) for p in points],
                         [70.0, None, 82.0])

    def test_last_snapshot_of_the_week_counts(self):
        """SC-DGQ-0034-1 (boundary): of two scans in one week the later one gives the point."""
        self.snap(50, '2026-10-27 08:00:00')
        self.snap(30, '2026-10-30 08:00:00')
        points = self.trend(period='week', model_id=self.contact.id)
        self.assertEqual([p['score'] for p in points], [70.0])

    def test_other_model_is_left_out_of_the_model_trend(self):
        """SC-DGQ-0034-1: the trend of Contact ignores a Product snapshot."""
        self.snap(30, '2026-10-28 10:00:00')
        self.snap(100, '2026-10-28 11:00:00', model=self.model('product.template'))
        points = self.trend(period='week', model_id=self.contact.id)
        self.assertEqual([p['score'] for p in points], [70.0])
