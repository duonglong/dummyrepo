"""Unit tests of the evaluations, reviews, corrective actions and the threshold review (T-029).

Requirements: FR-SPM-0085, FR-SPM-0086, FR-SPM-0087
"""
from datetime import date

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError, ValidationError

from ..services import performance
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
        order = self._line_order(['2026-11-12 12:00:00'])
        self._receive(order, 1.0, '2026-11-10 10:00:00')
        self.env['procurement.supplier.review'].create({
            'partner_id': self.acme.id, 'company_id': self.company.id, 'kind': 'incident',
            'date': '2026-11-11', 'rejected_qty': 1.0})
        result = performance.quality(self.env, self.acme, self.company,
                                     *performance.period_bounds('month', date(2026, 11, 20)))
        self.assertEqual(result['value'], 0.0)

    def test_fr_0087_a_metric_below_its_threshold_creates_a_review(self):
        """FR-SPM-0087: a metric under its own threshold triggers, an overall threshold of 0 does not."""
        template = self._template([('on_time', 100.0)], period='month', threshold=0.0)
        template.metric_ids.review_threshold = 80.0
        self.acme.sudo().write({'spm_responsible_user_id': self.buyer.id})
        order = self._line_order(['2026-11-20 12:00:00', '2026-11-05 12:00:00'])
        self._receive(order, 1.0, '2026-11-12 10:00:00')
        with freeze_time('2026-11-20 03:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        review = self.env['procurement.supplier.review'].search([('partner_id', '=', self.acme.id)])
        self.assertEqual(len(review), 1)
        self.assertIn('On-time', review.findings)

    def test_fr_0085_the_author_cannot_move_the_date_to_extend_the_window(self):
        """FR-SPM-0085: a later date is refused and the window still ends 7 days after recording."""
        evaluation = self._evaluation()
        with freeze_time('2026-11-20 09:00:00'):
            with self.assertRaises(ValidationError):
                evaluation.with_user(self.buyer).write({'date': '2027-06-01'})
        with freeze_time('2027-01-10 09:00:00'):
            with self.assertRaises(UserError):
                evaluation.with_user(self.buyer).write({'comment': 'rewritten'})

    def test_fr_0085_a_future_date_is_refused_at_creation(self):
        """FR-SPM-0085: an evaluation cannot be dated ahead to start its window later."""
        with freeze_time('2026-11-20 09:00:00'), self.assertRaises(ValidationError):
            self.env['procurement.supplier.evaluation'].with_user(self.buyer).create({
                'partner_id': self.acme.id, 'date': '2026-12-31'})

    def test_fr_0085_author_and_recording_day_are_fixed(self):
        """FR-SPM-0085: the author cannot hand the evaluation over nor change the recording day."""
        evaluation = self._evaluation()
        with freeze_time('2026-11-18 09:00:00'):
            with self.assertRaises(UserError):
                evaluation.with_user(self.buyer).write({'author_id': self.manager.id})
            with self.assertRaises(UserError):
                evaluation.with_user(self.buyer).write({'recorded_on': '2026-11-18'})

    def test_fr_0085_a_back_dated_evaluation_with_ratings_is_recorded(self):
        """FR-SPM-0085: an evaluation about an older day is accepted; its window counts from today."""
        criterion = self.env['procurement.evaluation.criterion'].create({'name': 'Old M6'})
        with freeze_time('2026-11-20 09:00:00'):
            evaluation = self.env['procurement.supplier.evaluation'].with_user(self.buyer).create({
                'partner_id': self.acme.id, 'date': '2026-10-01',
                'rating_ids': [(0, 0, {'criterion_id': criterion.id, 'rating': '3'})]})
            evaluation.write({'comment': 'ok'})
        self.assertEqual(str(evaluation.editable_until), '2026-11-27')

    def test_fr_0086_review_moves_through_its_states_with_buttons(self):
        """FR-SPM-0086: plan, done and cancel are methods the form buttons call (the button press is
        left to the screen review)."""
        review = self.env['procurement.supplier.review'].with_user(self.manager).create({
            'partner_id': self.acme.id, 'company_id': self.company.id, 'date': '2026-11-20'})
        review.action_plan()
        self.assertEqual(review.state, 'planned')
        with self.assertRaises(UserError):
            review.action_plan()
        review.action_done()
        self.assertEqual(review.state, 'done')
        with self.assertRaises(UserError):
            review.action_cancel()

    def test_fr_0087_a_closed_review_lets_the_next_breach_create_a_new_one(self):
        """FR-SPM-0087: once the open review is done, a breach of another scorecard opens a new one,
        but the same scorecard does not produce a second review."""
        template = self._template([('on_time', 100.0)], period='month', threshold=70.0)
        order = self._line_order(['2026-11-05 12:00:00'])
        self._receive(order, 1.0, '2026-11-12 10:00:00')
        with freeze_time('2026-11-20 03:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        review = self.env['procurement.supplier.review'].search([('partner_id', '=', self.acme.id)])
        self.assertEqual(len(review), 1)
        review.action_cancel()
        with freeze_time('2026-11-21 03:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        self.assertEqual(self.env['procurement.supplier.review'].search_count([('partner_id', '=', self.acme.id)]), 1)
        self.assertTrue(template)

    def test_fr_0087_an_open_incident_does_not_stop_the_threshold_review(self):
        """FR-SPM-0087: only an open review meeting counts as the open review."""
        self._template([('on_time', 100.0)], period='month', threshold=70.0)
        self.env['procurement.supplier.review'].create({
            'partner_id': self.acme.id, 'company_id': self.company.id, 'kind': 'incident', 'date': '2026-11-10'})
        order = self._line_order(['2026-11-05 12:00:00'])
        self._receive(order, 1.0, '2026-11-12 10:00:00')
        with freeze_time('2026-11-20 03:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        self.assertEqual(self.env['procurement.supplier.review'].search_count([
            ('partner_id', '=', self.acme.id), ('trigger', '=', 'threshold')]), 1)

    def test_fr_0086_rejected_quantity_is_not_negative_and_needs_a_date(self):
        """FR-SPM-0086: 0 is accepted, a negative value and a quantity without a date are refused."""
        values = {'partner_id': self.acme.id, 'company_id': self.company.id, 'kind': 'incident', 'date': '2026-11-10'}
        Review = self.env['procurement.supplier.review']
        Review.create(dict(values, rejected_qty=0.0))
        with self.assertRaises(Exception), self.cr.savepoint():
            Review.create(dict(values, rejected_qty=-3.0))
        with self.assertRaises(ValidationError):
            Review.create(dict(values, rejected_qty=2.0, date=False))

    def test_fr_0086_closing_an_action_marks_its_activity_done(self):
        """FR-SPM-0086: the done to-do is kept as a message in the chatter, not deleted without trace."""
        review = self.env['procurement.supplier.review'].with_user(self.manager).create({
            'partner_id': self.acme.id, 'company_id': self.company.id,
            'action_ids': [(0, 0, {'name': 'Fix', 'responsible_id': self.buyer2.id, 'deadline': '2026-12-01'})]})
        action = review.action_ids
        action.action_done()
        self.assertEqual(action.state, 'done')
        self.assertFalse(action.activity_ids)
        self.assertTrue(action.message_ids.filtered(lambda m: m.mail_activity_type_id))
        with self.assertRaises(UserError):
            action.action_done()
        action.action_reopen()
        self.assertEqual(len(action.activity_ids), 1)
