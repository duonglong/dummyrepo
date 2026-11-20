"""Unit tests of the evaluations, reviews, corrective actions and the threshold review (T-029).

Requirements: FR-SPM-0085, FR-SPM-0086, FR-SPM-0087
"""
from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError

from .scorecard_common import SpmScorecardCase


class TestReviews(SpmScorecardCase):
    """Requirements: FR-SPM-0085, FR-SPM-0086, FR-SPM-0087"""

    def _evaluation(self, when='2026-11-17'):
        criterion = self.env['procurement.evaluation.criterion'].create({'name': 'Cooperation M6'})
        with freeze_time(f'{when} 09:00:00'):
            return self.env['procurement.supplier.evaluation'].with_user(self.buyer).create({
                'partner_id': self.acme.id, 'date': when, 'comment': 'Fine',
                'rating_ids': [(0, 0, {'criterion_id': criterion.id, 'rating': '4'})]})

    def test_fr_0085_rating_changes_follow_the_edit_window(self):
        """FR-SPM-0085: a rating is changed by the author inside the window, by nobody after it."""
        evaluation = self._evaluation()
        with freeze_time('2026-11-20 09:00:00'):
            evaluation.rating_ids.with_user(self.buyer).write({'rating': '5'})
            with self.assertRaises(AccessError):
                evaluation.rating_ids.with_user(self.manager).write({'rating': '1'})
        with freeze_time('2026-11-25 09:00:00'):
            with self.assertRaises(UserError):
                evaluation.rating_ids.with_user(self.buyer).write({'rating': '1'})
            with self.assertRaises(UserError):
                evaluation.with_user(self.buyer).unlink()
        self.assertEqual(evaluation.rating_ids.rating, '5')

    def test_fr_0085_evaluation_is_recorded_under_the_callers_name(self):
        """FR-SPM-0085: nobody records an evaluation for another author."""
        with self.assertRaises(AccessError):
            self.env['procurement.supplier.evaluation'].with_user(self.buyer).create({
                'partner_id': self.acme.id, 'author_id': self.manager.id})

    def test_fr_0086_action_activity_follows_the_action(self):
        """FR-SPM-0086: closing an action removes its activity; the open count drops."""
        review = self.env['procurement.supplier.review'].with_user(self.manager).create({
            'partner_id': self.acme.id, 'company_id': self.company.id,
            'action_ids': [(0, 0, {'name': 'Fix', 'responsible_id': self.buyer2.id, 'deadline': '2026-12-01'})]})
        domain = [('user_id', '=', self.buyer2.id), ('res_model', '=', 'procurement.corrective.action')]
        self.assertEqual(self.env['mail.activity'].sudo().search_count(domain), 1)
        review.action_ids.write({'deadline': '2026-12-05'})
        activity = self.env['mail.activity'].sudo().search(domain)
        self.assertEqual(len(activity), 1)
        self.assertEqual(str(activity.date_deadline), '2026-12-05')
        review.action_ids.write({'state': 'done'})
        self.assertEqual(self.env['mail.activity'].sudo().search_count(domain), 0)
        self.assertEqual(self.acme.with_user(self.manager).spm_open_action_count, 0)

    def test_fr_0086_incident_rejected_quantity_lowers_quality(self):
        """FR-SPM-0086: a recorded incident's rejected quantity counts in the quality metric."""
        from ..services import performance
        order = self._line_order(['2026-11-12 12:00:00'])
        self._receive(order, 1.0, '2026-11-10 10:00:00')
        self.env['procurement.supplier.review'].create({
            'partner_id': self.acme.id, 'company_id': self.company.id, 'kind': 'incident',
            'date': '2026-11-11', 'rejected_qty': 1.0})
        result = performance.quality(self.env, self.acme, self.company,
                                     *performance.period_bounds('month', __import__('datetime').date(2026, 11, 20)))
        self.assertEqual(result['value'], 0.0)

    def test_fr_0087_a_metric_below_its_threshold_creates_a_review(self):
        """FR-SPM-0087: a metric under its own threshold triggers, an overall threshold of 0 does not."""
        template = self._template([('on_time', 100.0)], period='month', threshold=0.0)
        template.metric_ids.review_threshold = 80.0
        self.acme.sudo().write({'spm_responsible_user_id': self.buyer.id})
        order = self._line_order(['2026-11-20 12:00:00'] * 1 + ['2026-11-05 12:00:00'] * 1)
        self._receive(order, 1.0, '2026-11-12 10:00:00')
        with freeze_time('2026-11-20 03:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        review = self.env['procurement.supplier.review'].search([('partner_id', '=', self.acme.id)])
        self.assertEqual(len(review), 1)
        self.assertIn('On-time', review.findings)
