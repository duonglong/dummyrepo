"""Acceptance tests of T-029 (qualitative evaluations, supplier reviews, corrective actions,
threshold trigger), written from the specification.

Requirements: FR-SPM-0085, FR-SPM-0086, FR-SPM-0087, BR-SPM-0004

Names used (the design names none, fixed in tests/scorecard_common.py): the evaluation is
created with its ratings as one2many commands (criterion_id, rating '1' to '5'); a review
and its corrective actions are created the same way; the open actions of a supplier are
``spm_open_action_count``. The threshold review is made by the scorecard job.
"""
from datetime import date

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError, ValidationError

from .scorecard_common import SpmScorecardCase


class TestReviewsAcceptance(SpmScorecardCase):
    """Requirements: FR-SPM-0085, FR-SPM-0086, FR-SPM-0087"""

    def _evaluation(self, author=None, when='2026-11-17'):
        criterion = self.env['procurement.evaluation.criterion'].create({'name': 'Communication M6'})
        with freeze_time(f'{when} 09:00:00'):
            return self.env['procurement.supplier.evaluation'].with_user(author or self.buyer).create({
                'partner_id': self.acme.id, 'author_id': (author or self.buyer).id,
                'date': when, 'company_id': self.company.id, 'comment': 'Slow to answer',
                'rating_ids': [(0, 0, {'criterion_id': criterion.id, 'rating': '2'})],
            })

    def test_sc_0085_1_requester_and_supplier_portal_user_cannot_read_an_evaluation(self):
        """SC-SPM-0085-1: Ann (a Buyer) recorded an evaluation of Acme with communication 2 of 5
        and a comment; a Requester or an Acme portal user cannot read it."""
        evaluation = self._evaluation()
        self.env.flush_all()
        self.assertEqual(evaluation.author_id, self.buyer)
        self.assertEqual(evaluation.date, date(2026, 11, 17))
        self.assertEqual(evaluation.with_user(self.manager).comment, 'Slow to answer')
        for user in (self.requester, self.acme_user):
            with self.assertRaises(AccessError):
                evaluation.with_user(user).read(['comment'])
            with self.assertRaises(AccessError):
                evaluation.with_user(user).rating_ids.read(['rating'])

    def test_fr_0085_only_the_author_changes_an_evaluation_within_seven_days(self):
        """FR-SPM-0085: the author may change it 6 days later; another user may not; the author
        may not 8 days later."""
        evaluation = self._evaluation(when='2026-11-17')
        with freeze_time('2026-11-23 09:00:00'):
            evaluation.with_user(self.buyer).write({'comment': 'Better now'})
            self.assertEqual(evaluation.comment, 'Better now')
            with self.assertRaises((AccessError, UserError, ValidationError)):
                evaluation.with_user(self.manager).write({'comment': 'Not mine'})
        self.assertEqual(evaluation.comment, 'Better now')
        with freeze_time('2026-11-25 09:00:00'):
            with self.assertRaises((AccessError, UserError, ValidationError)):
                evaluation.with_user(self.buyer).write({'comment': 'Too late'})
        self.assertEqual(evaluation.comment, 'Better now')

    def test_sc_0086_1_a_review_action_is_open_and_gives_its_owner_an_activity(self):
        """SC-SPM-0086-1: a review meeting with Acme on 2026-11-17; the manager records the action
        'Weekly delivery forecast' for Ann by 2026-12-01: the action is Open on Acme and Ann has
        an activity due 2026-12-01."""
        ann = self.buyer2
        review = self.env['procurement.supplier.review'].with_user(self.manager).create({
            'partner_id': self.acme.id, 'kind': 'review', 'date': '2026-11-17',
            'state': 'done', 'company_id': self.company.id,
            'participant_ids': [(6, 0, (self.manager | ann).ids)],
            'findings': '<p>Late deliveries</p>',
            'action_ids': [(0, 0, {
                'name': 'Weekly delivery forecast', 'responsible_id': ann.id,
                'deadline': '2026-12-01', 'state': 'open'})],
        })
        self.env.flush_all()
        action = review.action_ids
        self.assertEqual(len(action), 1)
        self.assertEqual(action.state, 'open')
        self.assertEqual(self.acme.with_user(self.manager).spm_open_action_count, 1)
        activities = self.env['mail.activity'].sudo().search([
            ('user_id', '=', ann.id), ('date_deadline', '=', date(2026, 12, 1)),
            ('res_model', 'in', ('procurement.corrective.action', 'procurement.supplier.review')),
        ])
        self.assertEqual(len(activities), 1, "one activity for the one action")

    def test_sc_0087_1_a_low_score_creates_one_review_to_plan(self):
        """SC-SPM-0087-1: a review threshold of 70; Acme's new scorecard scores 64: one review of
        Acme to plan is created for its buyer; the scorecard is recomputed lower: no second
        review is created."""
        self.acme.sudo().write({'spm_responsible_user_id': self.buyer.id})
        template = self._template([('on_time', 100.0)], period='month', threshold=70.0)
        # 25 receipt lines done on 2026-11-12: 16 on time (planned later), 9 late
        order = self._line_order(['2026-11-20 12:00:00'] * 16 + ['2026-11-05 12:00:00'] * 9)
        self._receive(order, 1.0, '2026-11-12 10:00:00')
        with freeze_time('2026-11-20 03:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        card = self._scorecards(template)
        self.assertEqual(len(card), 1)
        self.assertAlmostEqual(card.score, 64.0, delta=0.5)
        reviews = self.env['procurement.supplier.review'].search([('partner_id', '=', self.acme.id)])
        self.assertEqual(len(reviews), 1, "one review is created")
        self.assertEqual(reviews.state, 'to_plan')
        self.assertEqual(reviews.trigger, 'threshold')
        self.assertEqual(reviews.responsible_id, self.buyer)
        # one more late line makes the score lower still (16 of 26)
        late = self._line_order(['2026-11-05 12:00:00'])
        self._receive(late, 1.0, '2026-11-13 10:00:00')
        with freeze_time('2026-11-21 03:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        card = self._scorecards(template)
        self.assertEqual(len(card), 1)
        self.assertLess(card.score, 64.0)
        reviews = self.env['procurement.supplier.review'].search([('partner_id', '=', self.acme.id)])
        self.assertEqual(len(reviews), 1, "an open review exists: no second one")

    def test_fr_0087_a_score_at_the_threshold_creates_no_review(self):
        """FR-SPM-0087: below the threshold only: a score equal to the threshold (16 of 25 against
        a threshold of 64) creates nothing."""
        self.acme.sudo().write({'spm_responsible_user_id': self.buyer.id})
        self._template([('on_time', 100.0)], period='month', threshold=64.0)
        order = self._line_order(['2026-11-20 12:00:00'] * 16 + ['2026-11-05 12:00:00'] * 9)
        self._receive(order, 1.0, '2026-11-12 10:00:00')
        with freeze_time('2026-11-20 03:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        self.env.flush_all()
        self.assertFalse(self.env['procurement.supplier.review'].search([('partner_id', '=', self.acme.id)]))
