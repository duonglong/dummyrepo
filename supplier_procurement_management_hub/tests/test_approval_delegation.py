"""Unit tests of T-020: delegation rights, escalation targets and the inbox action.

Requirements: FR-SPM-0050, FR-SPM-0051, FR-SPM-0054, BR-SPM-0019
"""
from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests import Form

from .approval_common import SpmApprovalCase


class TestApprovalDelegation(SpmApprovalCase):
    """Requirements: FR-SPM-0050, FR-SPM-0051, FR-SPM-0054"""

    def _delegate(self, delegator, delegate, date_from, date_to):
        return self.env['procurement.approval.delegation'].with_user(delegator).create({
            'delegator_id': delegator.id, 'delegate_id': delegate.id,
            'date_from': date_from, 'date_to': date_to})

    def _bob_step(self, when='2026-12-22 09:00:00'):
        self._policy('Amount', steps=[self._step('Bob', 10, users=self.bob)], amount_min=5000.0)
        with freeze_time(when):
            order = self._order()
            return order, self._submit(order).step_ids

    def test_a_delegation_is_created_for_the_session_user_by_default(self):
        """FR-SPM-0050: the delegation form starts with the session user as delegator."""
        form = Form(self.env['procurement.approval.delegation'].with_user(self.bob))
        self.assertEqual(form.delegator_id, self.bob)

    def test_nobody_creates_a_delegation_for_another_user(self):
        """FR-SPM-0050: a user cannot give away the approvals of somebody else."""
        with self.assertRaises(AccessError):
            self.env['procurement.approval.delegation'].with_user(self.eve).create({
                'delegator_id': self.bob.id, 'delegate_id': self.eve.id,
                'date_from': '2026-12-20', 'date_to': '2026-12-21'})

    def test_a_delegation_cannot_be_re_pointed_to_another_delegator(self):
        """FR-SPM-0050: an employee cannot take over the approvals of another approver by editing."""
        delegation = self._delegate(self.eve, self.bob, '2026-12-20', '2027-01-03')
        with self.assertRaises(AccessError):
            delegation.with_user(self.eve).write({'delegator_id': self.bob.id, 'delegate_id': self.eve.id})
        self.assertEqual(delegation.delegator_id, self.eve)
        delegation.with_user(self.eve).write({'delegator_id': self.eve.id, 'note': 'same'})

    def test_the_delegate_cannot_change_the_delegation(self):
        """FR-SPM-0050: only the delegator changes or removes his delegation."""
        delegation = self._delegate(self.bob, self.eve, '2026-12-20', '2027-01-03')
        with self.assertRaises(AccessError):
            delegation.with_user(self.eve).write({'date_to': '2030-01-01'})

    def test_a_delegate_stands_for_the_delegator_only_within_the_dates(self):
        """FR-SPM-0050: acting_for answers the delegator inside the dates, nobody outside."""
        order, step = self._bob_step()
        self._delegate(self.bob, self.eve, '2026-12-20', '2027-01-03')
        with freeze_time('2026-12-23 12:00:00'):
            self.assertEqual(step._spm_acting_for(self.eve), self.bob)
            self.assertEqual(step._spm_acting_for(self.bob), self.bob)
        with freeze_time('2027-01-04 12:00:00'):
            self.assertFalse(step._spm_acting_for(self.eve))

    def test_the_delegate_reads_the_steps_of_the_delegator_and_a_stranger_does_not(self):
        """FR-SPM-0054: the access rule lets the delegate read the delegator's steps, nobody else."""
        order, step = self._bob_step()
        self._delegate(self.bob, self.eve, '2026-12-20', '2027-01-03')
        self.assertEqual(step.with_user(self.eve).state, 'open')
        with self.assertRaises(AccessError):
            step.with_user(self.dave).read(['state'])

    def test_the_inbox_leaves_out_the_steps_of_an_ended_delegation(self):
        """FR-SPM-0054: the inbox follows the dates: the delegate sees the step inside them, not after."""
        order, step = self._bob_step()
        self._delegate(self.bob, self.eve, '2026-12-20', '2027-01-03')
        with freeze_time('2026-12-23 12:00:00'):
            rows = self.env['procurement.approval.step'].with_user(self.eve).spm_inbox()
            self.assertEqual([(r['step_id'], r['on_behalf_of_id']) for r in rows], [(step.id, self.bob.id)])
        with freeze_time('2027-01-04 12:00:00'):
            self.assertFalse(self.env['procurement.approval.step'].with_user(self.eve).spm_inbox())

    def test_the_inbox_action_lists_the_inbox_steps(self):
        """FR-SPM-0054: the action opens the steps of the inbox, in the inbox's order."""
        order, step = self._bob_step()
        action = self.env['procurement.approval.step'].with_user(self.bob).action_spm_open_inbox()
        self.assertEqual(action['domain'], [('id', 'in', step.ids)])

    def test_bug_b_0272_the_inbox_menu_action_runs_for_every_internal_role(self):
        """Regression test for B-0272: the Inbox menu entry opens for the roles that reach the menu.

        Requirements: FR-SPM-0054
        It cannot reach the rendered menu click: it runs the menu's server action as each user.
        """
        action = self.env.ref('supplier_procurement_management_hub.procurement_approval_inbox_action')
        for user in (self.bob, self.ann, self.buyer, self.auditor):
            with self.subTest(user=user.login):
                result = action.with_user(user).with_context(
                    active_model='procurement.approval.step').run()
                self.assertEqual(result['res_model'], 'procurement.approval.step')

    def test_a_step_without_escalation_target_is_still_marked(self):
        """FR-SPM-0051: no escalation user and no manager: the step is marked and the approver told, once."""
        order, step = self._bob_step('2026-11-14 09:00:00')
        with freeze_time('2026-11-16 10:00:00'):
            self.env['procurement.job']._spm_run('approval_escalation')
        self.assertTrue(step.escalated)

    def test_an_approver_who_decided_cannot_decide_again_as_delegate(self):
        """FR-SPM-0050: a decision is final for the delegator as well."""
        order, step = self._bob_step()
        self._delegate(self.bob, self.eve, '2026-12-20', '2027-01-03')
        with freeze_time('2026-12-23 12:00:00'):
            step.with_user(self.eve).action_spm_approve()
            with self.assertRaises(UserError):
                step.with_user(self.eve).action_spm_approve()

