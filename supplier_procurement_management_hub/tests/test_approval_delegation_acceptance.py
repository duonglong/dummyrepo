"""Acceptance tests of T-020 (approval delegation, escalation of overdue steps,
approval inbox), written from the specification.

Requirements: FR-SPM-0050, FR-SPM-0051, FR-SPM-0054, BR-SPM-0019

Names used (the design names none, fixed in tests/approval_common.py): the
delegation form is driven with Form (fields delegator_id, delegate_id,
date_from, date_to, note); the escalation runs as the job whose code holds
"approval" and "escalat" (procurement.job._spm_run(code)); the inbox is
procurement.approval.step.spm_inbox(), run as the user who opens it.
Delegation dates are whole days, compared in the company's time zone; the tests
act at midday so that the zone does not change the day.
"""
from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form

from .approval_common import SpmApprovalCase


class TestApprovalDelegationAcceptance(SpmApprovalCase):
    """Requirements: FR-SPM-0050, FR-SPM-0051, FR-SPM-0054"""

    def _delegation(self, delegator, delegate, date_from, date_to, user=None):
        form = Form(self.env['procurement.approval.delegation'].with_user(user or delegator))
        form.delegator_id = delegator
        form.delegate_id = delegate
        form.date_from = date_from
        form.date_to = date_to
        return form.save()

    def _bob_order_at(self, when, partner=None, approver=None, **policy):
        """Submit a 6,000 order at ``when`` whose only step is the approver's (Bob's)."""
        with freeze_time(when):
            order = self._order(partner)
            return order, self._submit(order)

    # FR-SPM-0050 -----------------------------------------------------------
    def test_sc_0050_1_the_delegate_decides_on_behalf_of_the_delegator(self):
        """SC-SPM-0050-1: Bob delegated to Eve from 2026-12-20 to 2027-01-03 and a step
        of Bob opens on 2026-12-22: Eve approves it and the decision shows Eve on
        behalf of Bob."""
        self._policy('Amount', steps=[self._step('Bob', 10, users=self.bob)], amount_min=5000.0)
        self._delegation(self.bob, self.eve, '2026-12-20', '2027-01-03')
        order, request = self._bob_order_at('2026-12-22 09:00:00')
        with freeze_time('2026-12-23 12:00:00'):
            request.step_ids.with_user(self.eve).action_spm_approve('Covering for Bob')
        self.env.flush_all()
        decision = request.step_ids.decision_ids
        self.assertEqual(decision.approver_id, self.eve)
        self.assertEqual(decision.on_behalf_of_id, self.bob)
        self.assertEqual(decision.decision, 'approved')
        self.assertEqual(request.step_ids.state, 'approved')
        self.assertEqual(order.spm_approval_state, 'approved')

    def test_sc_0050_2_after_the_delegation_ended_the_delegate_is_refused(self):
        """SC-SPM-0050-2: the same delegation; Eve tries to approve one of Bob's
        steps on 2027-01-04: refused, the delegation ended."""
        self._policy('Amount', steps=[self._step('Bob', 10, users=self.bob)], amount_min=5000.0)
        self._delegation(self.bob, self.eve, '2026-12-20', '2027-01-03')
        order, request = self._bob_order_at('2026-12-22 09:00:00')
        with freeze_time('2027-01-04 12:00:00'):
            with self.assertRaises((AccessError, UserError)):
                request.step_ids.with_user(self.eve).action_spm_approve()
        self.assertEqual(request.step_ids.state, 'open')
        self.assertFalse(request.step_ids.decision_ids)

    def test_sc_0050_2_the_first_and_the_last_day_count_and_the_day_before_does_not(self):
        """FR-SPM-0050 boundaries: Eve may decide on 2027-01-03 (the last day) and on
        2026-12-20 (the first day), but not on 2026-12-19."""
        self._policy('Amount', steps=[self._step('Bob', 10, users=self.bob)], amount_min=5000.0)
        self._delegation(self.bob, self.eve, '2026-12-20', '2027-01-03')
        order, request = self._bob_order_at('2026-12-10 09:00:00')
        with freeze_time('2026-12-19 12:00:00'):
            with self.assertRaises((AccessError, UserError)):
                request.step_ids.with_user(self.eve).action_spm_approve()
        order2, request2 = self._bob_order_at('2026-12-10 09:00:00')
        with freeze_time('2026-12-20 12:00:00'):
            request2.step_ids.with_user(self.eve).action_spm_approve()
        self.assertEqual(request2.step_ids.state, 'approved')
        order3, request3 = self._bob_order_at('2026-12-10 09:00:00')
        with freeze_time('2027-01-03 12:00:00'):
            request3.step_ids.with_user(self.eve).action_spm_approve()
        self.assertEqual(request3.step_ids.decision_ids.on_behalf_of_id, self.bob)

    def test_sc_0050_1_the_delegator_can_still_decide(self):
        """FR-SPM-0050: delegating does not take the right away from Bob."""
        self._policy('Amount', steps=[self._step('Bob', 10, users=self.bob)], amount_min=5000.0)
        self._delegation(self.bob, self.eve, '2026-12-20', '2027-01-03')
        order, request = self._bob_order_at('2026-12-22 09:00:00')
        with freeze_time('2026-12-23 12:00:00'):
            request.step_ids.with_user(self.bob).action_spm_approve()
        decision = request.step_ids.decision_ids
        self.assertEqual(decision.approver_id, self.bob)
        self.assertFalse(decision.on_behalf_of_id)

    def test_sc_0050_1_a_delegation_does_not_cover_other_approvers_steps(self):
        """FR-SPM-0050: Eve's delegation from Bob does not let her decide Carla's step."""
        self._policy('Amount', steps=[self._step('Carla', 10, users=self.carla)], amount_min=5000.0)
        self._delegation(self.bob, self.eve, '2026-12-20', '2027-01-03')
        order, request = self._bob_order_at('2026-12-22 09:00:00')
        with freeze_time('2026-12-23 12:00:00'):
            with self.assertRaises((AccessError, UserError)):
                request.step_ids.with_user(self.eve).action_spm_approve()

    def test_sc_0050_1_a_delegation_to_oneself_is_refused(self):
        """FR-SPM-0050: a delegation to oneself is refused."""
        with self.assertRaises(ValidationError):
            self._delegation(self.bob, self.bob, '2026-12-20', '2027-01-03')

    def test_sc_0050_1_an_end_date_before_the_start_date_is_refused(self):
        """FR-SPM-0050 boundary: an end date before the start date is refused; a
        one-day delegation (same date) is allowed."""
        with self.assertRaises(ValidationError):
            self._delegation(self.bob, self.eve, '2026-12-20', '2026-12-19')
        one_day = self._delegation(self.bob, self.eve, '2026-12-20', '2026-12-20')
        self.assertEqual(one_day.date_from, one_day.date_to)

    # FR-SPM-0051 -----------------------------------------------------------
    def _escalation_code(self):
        selection = dict(self.env['procurement.job']._fields['code'].selection)
        keys = [k for k in selection if 'approval' in k and 'escal' in k]
        self.assertTrue(keys, "an approval escalation job exists")
        return keys[0]

    def _run_job(self, when):
        with freeze_time(when):
            self.env['procurement.job']._spm_run(self._escalation_code())
            self.env.flush_all()

    def _activities(self, order, user):
        return self.env['mail.activity'].sudo().search([
            ('res_model', '=', 'purchase.order'), ('res_id', '=', order.id),
            ('user_id', '=', user.id)])

    def _escalation_case(self, **policy):
        self._policy('Amount', steps=[self._step('Bob', 10, users=self.bob)],
                     amount_min=5000.0, **policy)
        return self._bob_order_at('2026-11-14 09:00:00')

    def test_sc_0051_1_an_overdue_step_creates_an_activity_for_the_escalation_user(self):
        """SC-SPM-0051-1: a step open since 2026-11-14 09:00 with a 48-hour escalation
        delay and escalation user Carla; the job runs on 2026-11-16 10:00: Carla has an
        activity on the order and the step is marked escalated."""
        order, request = self._escalation_case(escalation_hours=48, escalation_user_id=self.carla.id)
        self._run_job('2026-11-16 10:00:00')
        self.assertEqual(len(self._activities(order, self.carla)), 1)
        step = request.step_ids
        self.assertTrue(step.escalated)
        self.assertTrue(step.escalated_date)
        self.assertEqual(step.state, 'open')

    def test_sc_0051_1_the_default_delay_is_48_hours(self):
        """FR-SPM-0051: a policy that does not set the delay escalates after 48 hours:
        not at 2026-11-16 08:59, yes at 10:00."""
        order, request = self._escalation_case(escalation_user_id=self.carla.id)
        self._run_job('2026-11-16 08:59:00')
        self.assertFalse(request.step_ids.escalated)
        self.assertFalse(self._activities(order, self.carla))
        self._run_job('2026-11-16 10:00:00')
        self.assertTrue(request.step_ids.escalated)
        self.assertEqual(len(self._activities(order, self.carla)), 1)

    def test_sc_0051_1_the_approvers_manager_is_the_fallback(self):
        """FR-SPM-0051: with no escalation user the activity goes to the approver's
        manager (Bob reports to Carla)."""
        self._employees()
        order, request = self._escalation_case(escalation_hours=48)
        self._run_job('2026-11-16 10:00:00')
        self.assertEqual(len(self._activities(order, self.carla)), 1)
        self.assertTrue(request.step_ids.escalated)

    def test_sc_0051_1_the_decision_stays_with_the_approver(self):
        """FR-SPM-0051: escalation does not move the decision: Bob can still approve."""
        order, request = self._escalation_case(escalation_hours=48, escalation_user_id=self.carla.id)
        self._run_job('2026-11-16 10:00:00')
        with freeze_time('2026-11-16 11:00:00'):
            request.step_ids.with_user(self.bob).action_spm_approve()
        self.assertEqual(request.step_ids.state, 'approved')

    def test_sc_0051_2_a_second_run_escalates_nothing_again(self):
        """SC-SPM-0051-2: the step is already escalated; the job runs again: no second
        activity and no second notification."""
        order, request = self._escalation_case(escalation_hours=48, escalation_user_id=self.carla.id)
        self._run_job('2026-11-16 10:00:00')
        messages = self.env['mail.message'].sudo().search_count([
            ('model', '=', 'purchase.order'), ('res_id', '=', order.id)])
        mails = self.env['mail.mail'].sudo().search_count([])
        escalated_date = request.step_ids.escalated_date
        self._run_job('2026-11-16 12:00:00')
        self._run_job('2026-11-17 12:00:00')
        self.assertEqual(len(self._activities(order, self.carla)), 1)
        self.assertEqual(self.env['mail.message'].sudo().search_count([
            ('model', '=', 'purchase.order'), ('res_id', '=', order.id)]), messages)
        self.assertEqual(self.env['mail.mail'].sudo().search_count([]), mails)
        self.assertEqual(request.step_ids.escalated_date, escalated_date)

    def test_sc_0051_2_a_decided_step_is_not_escalated(self):
        """FR-SPM-0051: only open steps are escalated; an approved step is left alone."""
        order, request = self._escalation_case(escalation_hours=48, escalation_user_id=self.carla.id)
        with freeze_time('2026-11-15 09:00:00'):
            request.step_ids.with_user(self.bob).action_spm_approve()
        self._run_job('2026-11-16 10:00:00')
        self.assertFalse(request.step_ids.escalated)
        self.assertFalse(self._activities(order, self.carla))

    # FR-SPM-0054 -----------------------------------------------------------
    def test_sc_0054_1_the_inbox_lists_own_and_delegated_steps_oldest_first(self):
        """SC-SPM-0054-1: Bob has one open step of his own, Eve delegated her approvals
        to Bob for today and Eve has two open steps: Bob opens the inbox and sees three
        steps, the delegated ones marked on behalf of Eve, oldest first."""
        gamma = self.env['res.partner'].create({'name': 'Gamma M3', 'is_company': True})
        self._policy('Acme', 10, steps=[self._step('Eve A', 10, users=self.eve)],
                     amount_min=5000.0, partner_ids=self.acme)
        self._policy('Beta', 20, steps=[self._step('Bob', 10, users=self.bob)],
                     amount_min=5000.0, partner_ids=self.beta)
        self._policy('Gamma', 30, steps=[self._step('Eve B', 10, users=self.eve)],
                     amount_min=5000.0, partner_ids=gamma)
        first, first_request = self._bob_order_at('2026-12-01 09:00:00', partner=self.acme)
        second, second_request = self._bob_order_at('2026-12-02 09:00:00', partner=self.beta)
        third, third_request = self._bob_order_at('2026-12-03 09:00:00', partner=gamma)
        self._delegation(self.eve, self.bob, '2026-12-04', '2026-12-04')
        with freeze_time('2026-12-04 12:00:00'):
            rows = self.env['procurement.approval.step'].with_user(self.bob).spm_inbox()
        self.assertEqual(
            [(row['step_id'], row['on_behalf_of_id']) for row in rows],
            [(first_request.step_ids.id, self.eve.id),
             (second_request.step_ids.id, False),
             (third_request.step_ids.id, self.eve.id)])
        self.assertEqual([row['order_id'] for row in rows], [first.id, second.id, third.id])

    def test_sc_0054_1_without_a_delegation_the_inbox_has_own_steps_only(self):
        """FR-SPM-0054: a delegation of another day adds nothing, and a decided step
        leaves the inbox."""
        self._policy('Acme', 10, steps=[self._step('Eve', 10, users=self.eve)],
                     amount_min=5000.0, partner_ids=self.acme)
        self._policy('Beta', 20, steps=[self._step('Bob', 10, users=self.bob)],
                     amount_min=5000.0, partner_ids=self.beta)
        eve_order, eve_request = self._bob_order_at('2026-12-01 09:00:00', partner=self.acme)
        bob_order, bob_request = self._bob_order_at('2026-12-02 09:00:00', partner=self.beta)
        self._delegation(self.eve, self.bob, '2026-12-10', '2026-12-12')
        with freeze_time('2026-12-04 12:00:00'):
            rows = self.env['procurement.approval.step'].with_user(self.bob).spm_inbox()
            self.assertEqual([row['step_id'] for row in rows], [bob_request.step_ids.id])
            bob_request.step_ids.with_user(self.bob).action_spm_approve()
            self.assertFalse(self.env['procurement.approval.step'].with_user(self.bob).spm_inbox())
