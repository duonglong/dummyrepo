"""Acceptance tests of T-019 (order approval flow: submit, confirmation guard,
decisions, material change), written from the specification.

Requirements: BR-SPM-0010, FR-SPM-0047, FR-SPM-0048, FR-SPM-0049, FR-SPM-0052,
FR-SPM-0053, plus the submission outcomes of FR-SPM-0044, FR-SPM-0045, FR-SPM-0046

Names used (the design names only action_spm_submit_approval; the rest is fixed
in tests/approval_common.py): step.action_spm_approve(comment=None) and
step.action_spm_reject(reason=None) are the buttons of the order's Approval
panel; the reason dialog is left to the build. The order fields are those of
the design: spm_controlled, spm_approval_state (none, not_required, pending,
approved, rejected), spm_revision, spm_approval_request_ids, spm_requester_id.
"""
from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError, ValidationError

from .approval_common import SpmApprovalCase


class TestApprovalFlowAcceptance(SpmApprovalCase):
    """Requirements: FR-SPM-0047, FR-SPM-0048, FR-SPM-0049, FR-SPM-0052, FR-SPM-0053"""

    def _amount_policy(self, **extra):
        return self._policy(
            'Amount', steps=[self._step('Bob', 10, users=self.bob)], amount_min=5000.0, **extra)

    def _approved_order(self, **kwargs):
        self._amount_policy(**kwargs.pop('policy', {}))
        order = self._order(**kwargs)
        self._submit(order)
        self._approve_open_steps(order)
        self.assertEqual(order.spm_approval_state, 'approved')
        return order

    # FR-SPM-0047 -----------------------------------------------------------
    def test_sc_0047_1_submit_creates_a_request_and_controls_the_order(self):
        """SC-SPM-0047-1: an amount policy from 5,000 with a manager step; the Buyer
        submits a 6,000 order: one approval request with one manager step exists, the
        order is marked To approve (pending) and controlled, with its snapshot."""
        self._employees()
        self._policy('Amount', steps=[self._step(
            'Manager', 10, approver_type='manager', manager_levels=1)], amount_min=5000.0)
        order = self._order(requester=self.ann)
        request = self._submit(order)
        self.assertEqual(len(order.spm_approval_request_ids), 1)
        self.assertEqual(request.state, 'pending')
        self.assertEqual(len(request.step_ids), 1)
        self.assertEqual(request.step_ids.approver_ids, self.bob)
        self.assertEqual(request.step_ids.state, 'open')
        self.assertTrue(order.spm_controlled)
        self.assertEqual(order.spm_approval_state, 'pending')
        self.assertEqual(request.revision, order.spm_revision)
        self.assertEqual(request.snapshot_amount, 6000.0)
        self.assertEqual(request.snapshot_partner_id, self.acme)
        self.assertEqual(request.snapshot_currency_id, order.currency_id)
        self.assertEqual(request.submitted_by_id, self.buyer)
        self.assertEqual(order.state, 'draft')

    def test_sc_0047_2_no_matching_policy_needs_no_approval(self):
        """SC-SPM-0047-2: no policy matches a 300 order: no request is created and
        the order can be confirmed."""
        self._amount_policy()
        order = self._order(price=30.0, qty=10.0)
        self._submit(order)
        self.assertFalse(order.spm_approval_request_ids)
        self.assertEqual(order.spm_approval_state, 'not_required')
        self.assertFalse(order.spm_controlled)
        order.with_user(self.buyer).button_confirm()
        self.assertEqual(order.state, 'purchase')

    def test_sc_0044_2_submission_is_refused_when_a_step_has_no_approver(self):
        """SC-SPM-0044-2: a group step 'Finance approvers' with no active member; the
        submission is refused with a message naming the step and the order is not controlled."""
        group = self.env['res.groups'].create({'name': 'Finance approvers M3c'})
        self._policy('Finance', steps=[self._step(
            'Finance approvers', 10, approver_type='group', group_id=group.id)], amount_min=5000.0)
        order = self._order()
        with self.assertRaises(UserError) as ctx:
            order.with_user(self.buyer).action_spm_submit_approval()
        self.assertIn('Finance approvers', str(ctx.exception))
        self.assertFalse(order.spm_approval_request_ids)
        self.assertFalse(order.spm_controlled)

    def test_sc_0045_1_a_later_sequence_waits_for_the_earlier_one(self):
        """SC-SPM-0045-1: steps IT (10), Security (10) and Finance (20), all 'any': after
        the submission IT and Security are open and Finance waits; when IT and Security
        approve, Finance opens."""
        self._policy('Laptops', steps=[
            self._step('IT', 10, users=self.bob), self._step('Security', 10, users=self.carla),
            self._step('Finance', 20, users=self.eve)], amount_min=5000.0)
        order = self._order()
        request = self._submit(order)
        steps = {s.name: s for s in request.step_ids}
        self.assertEqual((steps['IT'].state, steps['Security'].state, steps['Finance'].state),
                         ('open', 'open', 'waiting'))
        steps['IT'].with_user(self.bob).action_spm_approve()
        self.env.flush_all()
        self.assertEqual(steps['Finance'].state, 'waiting')
        steps['Security'].with_user(self.carla).action_spm_approve()
        self.env.flush_all()
        self.assertEqual(steps['Finance'].state, 'open')
        self.assertTrue(steps['Finance'].opened_date)
        self.assertEqual(request.state, 'pending')
        steps['Finance'].with_user(self.eve).action_spm_approve()
        self.assertEqual(request.state, 'approved')
        self.assertEqual(order.spm_approval_state, 'approved')

    def test_sc_0045_2_an_all_step_waits_for_every_approver(self):
        """SC-SPM-0045-2: a step of mode all with approvers Bob and Carla: when only
        Bob approves the step stays open waiting for Carla."""
        self._policy('Both', steps=[self._step(
            'Both', 10, users=self.bob | self.carla, mode='all')], amount_min=5000.0)
        order = self._order()
        request = self._submit(order)
        step = request.step_ids
        step.with_user(self.bob).action_spm_approve()
        self.env.flush_all()
        self.assertEqual(step.state, 'open')
        self.assertEqual(request.state, 'pending')
        self.assertEqual(step.decision_ids.approver_id, self.bob)
        step.with_user(self.carla).action_spm_approve()
        self.assertEqual(step.state, 'approved')
        self.assertEqual(request.state, 'approved')

    def test_sc_0045_2_an_any_step_is_decided_by_one_approver(self):
        """FR-SPM-0045: a step of mode any is approved by one approver; the other
        cannot decide it afterwards."""
        self._policy('Either', steps=[self._step(
            'Either', 10, users=self.bob | self.carla, mode='any')], amount_min=5000.0)
        request = self._submit(self._order())
        request.step_ids.with_user(self.bob).action_spm_approve()
        self.assertEqual(request.step_ids.state, 'approved')
        with self.assertRaises((UserError, AccessError)):
            request.step_ids.with_user(self.carla).action_spm_approve()
        self.assertEqual(len(request.step_ids.decision_ids), 1)

    def test_sc_0046_1_the_request_shows_the_cumulated_amount(self):
        """SC-SPM-0046-1: a monthly cumulative policy from 10,000 for vendor Acme, 8,000
        of Acme orders confirmed this month: an Acme order of 3,000 is submitted, the
        policy matches and the request shows a cumulated amount of 11,000."""
        with freeze_time('2026-11-17 09:00:00'):
            self._policy('Acme month', steps=[self._step('Bob', 10, users=self.bob)],
                         amount_min=10000.0, partner_ids=self.acme, cumulative_period='month')
            earlier = self._order(price=8000.0, qty=1.0, date_order='2026-11-02 09:00:00')
            earlier.button_confirm()
            order = self._order(price=3000.0, qty=1.0)
            request = self._submit(order)
        self.assertEqual(len(request), 1)
        self.assertAlmostEqual(request.cumulated_amount, 11000.0, places=2)
        self.assertEqual(order.spm_approval_state, 'pending')

    # FR-SPM-0048 -----------------------------------------------------------
    def test_sc_0048_1_a_pending_order_cannot_be_confirmed_on_any_route(self):
        """SC-SPM-0048-1: a controlled order with a pending request: confirming it
        through an external call is refused with a message that approval is pending;
        so are the approval button with force and a direct write of the state."""
        self._amount_policy()
        order = self._order()
        self._submit(order)
        buyer_order = self.env['purchase.order'].with_user(self.buyer).browse(order.id)
        with self.assertRaises(UserError) as ctx:
            buyer_order.button_confirm()
        self.assertIn('pending', str(ctx.exception).lower())
        with self.assertRaises(UserError):
            buyer_order.button_approve(force=True)
        with self.assertRaises(UserError):
            buyer_order.write({'state': 'purchase'})
        self.assertEqual(order.state, 'draft')

    def test_sc_0048_2_an_approved_order_is_confirmed_by_the_purchase_flow(self):
        """SC-SPM-0048-2: a controlled order whose request is approved for its current
        revision is confirmed by the Buyer through Odoo's own flow."""
        order = self._approved_order()
        order.with_user(self.buyer).button_confirm()
        self.assertEqual(order.state, 'purchase')

    def test_sc_0048_1_orders_that_were_never_controlled_are_not_affected(self):
        """FR-SPM-0048: an order never submitted (as one created before the app was
        installed) is confirmed as before, even with a policy that would match."""
        self._amount_policy()
        order = self._order()
        self.assertFalse(order.spm_controlled)
        order.with_user(self.buyer).button_confirm()
        self.assertEqual(order.state, 'purchase')

    def test_sc_0048_1_a_rejected_order_cannot_be_confirmed_either(self):
        """FR-SPM-0048: a controlled order whose request was rejected is refused too."""
        self._amount_policy()
        order = self._order()
        request = self._submit(order)
        request.step_ids.with_user(self.bob).action_spm_reject('Too expensive')
        with self.assertRaises(UserError):
            order.with_user(self.buyer).button_confirm()

    # FR-SPM-0049 -----------------------------------------------------------
    def test_sc_0049_1_rejection_with_a_reason_returns_the_order_to_draft(self):
        """SC-SPM-0049-1: Bob rejects with the reason 'Use the framework contract
        supplier': the request is Rejected, the order is draft and the Buyer is
        notified with the reason; the decision is recorded."""
        self._amount_policy()
        order = self._order()
        request = self._submit(order)
        request.step_ids.with_user(self.bob).action_spm_reject('Use the framework contract supplier')
        self.env.flush_all()
        self.assertEqual(request.state, 'rejected')
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.spm_approval_state, 'rejected')
        self.assertEqual(request.step_ids.state, 'rejected')
        decision = request.step_ids.decision_ids
        self.assertEqual((decision.approver_id, decision.decision), (self.bob, 'rejected'))
        self.assertEqual(decision.comment, 'Use the framework contract supplier')
        self.assertTrue(self._sent_to(self.buyer.partner_id, 'Use the framework contract supplier'))

    def test_sc_0049_1_a_rejected_order_can_be_changed_and_resubmitted(self):
        """FR-SPM-0049: after a rejection the Buyer resubmits: a new request is
        created and the rejected one stays in the history."""
        self._amount_policy()
        order = self._order()
        first = self._submit(order)
        first.step_ids.with_user(self.bob).action_spm_reject('Too expensive')
        order.order_line.with_user(self.buyer).write({'price_unit': 550.0})
        second = self._submit(order)
        self.assertNotEqual(first, second)
        self.assertEqual(first.state, 'rejected')
        self.assertEqual(second.state, 'pending')
        self.assertEqual(len(order.spm_approval_request_ids), 2)
        self.assertEqual(order.spm_approval_state, 'pending')

    def test_sc_0049_2_rejection_without_a_reason_is_refused(self):
        """SC-SPM-0049-2: Bob rejects without a reason: refused, the step stays open
        and nothing is recorded."""
        self._amount_policy()
        request = self._submit(self._order())
        for reason in (None, '', '   '):
            with self.assertRaises((UserError, ValidationError)):
                request.step_ids.with_user(self.bob).action_spm_reject(reason)
        self.env.flush_all()
        self.assertEqual(request.step_ids.state, 'open')
        self.assertEqual(request.state, 'pending')
        self.assertFalse(request.step_ids.decision_ids)

    def test_sc_0049_3_someone_who_is_not_an_approver_cannot_approve(self):
        """SC-SPM-0049-3: Dave is not an approver of the open step: his approval is
        refused with an access error and the step stays open."""
        self._amount_policy()
        request = self._submit(self._order())
        with self.assertRaises(AccessError):
            request.step_ids.with_user(self.dave).action_spm_approve()
        with self.assertRaises(AccessError):
            request.step_ids.with_user(self.dave).action_spm_reject('No')
        self.assertEqual(request.step_ids.state, 'open')
        self.assertFalse(request.step_ids.decision_ids)

    def test_sc_0049_3_a_waiting_step_cannot_be_decided(self):
        """FR-SPM-0045 and FR-SPM-0049: Eve's step waits for Bob's; her approval is refused."""
        self._policy('Chain', steps=[
            self._step('First', 10, users=self.bob), self._step('Second', 20, users=self.eve)],
            amount_min=5000.0)
        request = self._submit(self._order())
        second = request.step_ids.filtered(lambda s: s.name == 'Second')
        with self.assertRaises((UserError, AccessError)):
            second.with_user(self.eve).action_spm_approve()
        self.assertEqual(second.state, 'waiting')

    # BR-SPM-0010, FR-SPM-0052 ----------------------------------------------
    def test_sc_0052_1_a_higher_price_cancels_the_approval_and_starts_again(self):
        """SC-SPM-0052-1: an approved order of 6,000 with tolerance 0; the Buyer raises
        a unit price so the total becomes 6,100: the request is cancelled with the reason
        material change, a new request is created on the new revision and the order
        cannot be confirmed."""
        order = self._approved_order()
        old = self._request(order)
        order.order_line.with_user(self.buyer).write({'price_unit': 610.0})
        self.env.flush_all()
        self.assertEqual(old.state, 'cancelled')
        self.assertEqual(old.cancel_reason, 'material_change')
        new = self._request(order)
        self.assertNotEqual(new, old)
        self.assertEqual(new.state, 'pending')
        self.assertEqual(order.spm_revision, 2)
        self.assertEqual(new.revision, 2)
        self.assertEqual(new.snapshot_amount, 6100.0)
        self.assertEqual(order.spm_approval_state, 'pending')
        with self.assertRaises(UserError):
            order.with_user(self.buyer).button_confirm()

    def test_sc_0052_2_a_delivery_date_keeps_the_approval(self):
        """SC-SPM-0052-2: an approved order; the Buyer changes only a line's expected
        date: the approval stays valid and the order is confirmed."""
        order = self._approved_order()
        request = self._request(order)
        order.order_line.with_user(self.buyer).write({'date_planned': '2027-02-01 08:00:00'})
        self.env.flush_all()
        self.assertEqual(request.state, 'approved')
        self.assertEqual(order.spm_revision, 1)
        self.assertEqual(order.spm_approval_state, 'approved')
        order.with_user(self.buyer).button_confirm()
        self.assertEqual(order.state, 'purchase')

    def test_sc_0052_2_a_lower_total_is_not_material(self):
        """BR-SPM-0010: a decrease of the total keeps the approval."""
        order = self._approved_order()
        order.order_line.with_user(self.buyer).write({'price_unit': 590.0})
        self.assertEqual(self._request(order).state, 'approved')
        self.assertEqual(order.spm_revision, 1)

    def test_sc_0052_1_the_tolerance_of_the_policy_is_the_limit(self):
        """BR-SPM-0010: with a re-approval tolerance of 100 an increase of exactly 100
        keeps the approval and an increase of 100.10 cancels it."""
        order = self._approved_order(policy={'reapproval_tolerance': 100.0})
        order.order_line.with_user(self.buyer).write({'price_unit': 610.0})
        self.assertEqual(self._request(order).state, 'approved')
        self.assertEqual(order.spm_revision, 1)
        order.order_line.with_user(self.buyer).write({'price_unit': 610.01})
        self.env.flush_all()
        self.assertEqual(order.spm_revision, 2)
        self.assertEqual(self._request(order).state, 'pending')

    def test_sc_0052_1_a_change_of_vendor_is_material(self):
        """BR-SPM-0010: another vendor needs a new approval even with the same total."""
        order = self._approved_order()
        old = self._request(order)
        order.with_user(self.buyer).write({'partner_id': self.beta.id})
        self.env.flush_all()
        self.assertEqual(old.state, 'cancelled')
        self.assertEqual(old.cancel_reason, 'material_change')
        self.assertEqual(self._request(order).snapshot_partner_id, self.beta)

    def test_sc_0052_1_an_added_line_is_material_even_without_value(self):
        """BR-SPM-0010: any added line is material; a line at price 0 does not
        change the total but cancels the approval."""
        order = self._approved_order()
        old = self._request(order)
        order.with_user(self.buyer).write({'order_line': [(0, 0, {
            'product_id': self.chair.id, 'name': 'Free sample', 'product_qty': 1.0,
            'price_unit': 0.0, 'tax_ids': [(6, 0, [])]})]})
        self.env.flush_all()
        self.assertEqual(old.state, 'cancelled')
        self.assertEqual(order.spm_revision, 2)

    def test_sc_0052_1_a_removed_line_is_material(self):
        """BR-SPM-0010: any removed line is material."""
        extra = (0, 0, {'product_id': self.chair.id, 'name': 'Sample', 'product_qty': 1.0,
                        'price_unit': 0.0, 'tax_ids': [(6, 0, [])]})
        order = self._approved_order(extra_lines=[extra])
        old = self._request(order)
        order.order_line.filtered(lambda l: not l.price_unit).with_user(self.buyer).unlink()
        self.env.flush_all()
        self.assertEqual(old.state, 'cancelled')
        self.assertEqual(order.spm_revision, 2)

    def test_sc_0052_1_a_draft_that_was_never_submitted_changes_freely(self):
        """FR-SPM-0052: only an order to approve or approved is re-evaluated; a plain
        draft keeps revision 1 whatever its price."""
        self._amount_policy()
        order = self._order()
        order.order_line.with_user(self.buyer).write({'price_unit': 900.0})
        self.assertEqual(order.spm_revision, 1)
        self.assertFalse(order.spm_approval_request_ids)

    # FR-SPM-0053 -----------------------------------------------------------
    def test_sc_0053_1_the_decision_keeps_the_revision_it_applied_to(self):
        """SC-SPM-0053-1: Bob approved revision 2 of an order of 6,000 with Acme: the
        Auditor sees Bob, approved, the time, the comment, revision 2, 6,000 and Acme,
        and cannot edit or delete it."""
        self._amount_policy()
        order = self._order(price=550.0)
        self._submit(order)
        order.order_line.with_user(self.buyer).write({'price_unit': 600.0})
        self.assertEqual(order.spm_revision, 2)
        request = self._request(order)
        with freeze_time('2026-11-20 14:30:00'):
            request.step_ids.with_user(self.bob).action_spm_approve('Fine with me')
        self.env.flush_all()
        decision = self.env['procurement.approval.decision'].with_user(self.auditor).search(
            [('request_id', '=', request.id)])
        self.assertEqual(len(decision), 1)
        self.assertEqual(decision.approver_id, self.bob)
        self.assertEqual(decision.decision, 'approved')
        self.assertEqual(str(decision.date), '2026-11-20 14:30:00')
        self.assertEqual(decision.comment, 'Fine with me')
        self.assertEqual(decision.revision, 2)
        self.assertEqual(decision.amount, 6000.0)
        self.assertEqual(decision.currency_id, order.currency_id)
        self.assertEqual(decision.partner_id, self.acme)
        self.assertFalse(decision.on_behalf_of_id)
        with self.assertRaises((AccessError, UserError)):
            decision.write({'comment': 'Changed'})
        with self.assertRaises((AccessError, UserError)):
            decision.unlink()

    def test_sc_0053_1_no_one_not_even_the_superuser_changes_a_decision(self):
        """FR-SPM-0053: write and delete are refused for everyone."""
        self._amount_policy()
        request = self._submit(self._order())
        request.step_ids.with_user(self.bob).action_spm_approve('Fine')
        decision = request.step_ids.decision_ids
        with self.assertRaises((AccessError, UserError)):
            decision.sudo().write({'comment': 'Changed'})
        with self.assertRaises((AccessError, UserError)):
            decision.sudo().unlink()
        self.assertEqual(decision.comment, 'Fine')

    def test_sc_0053_1_a_cancelled_request_keeps_its_decisions(self):
        """FR-SPM-0053: the decision of an approval cancelled by a material change
        stays with its own revision (1), next to the new request."""
        order = self._approved_order()
        old_decisions = self._request(order).step_ids.decision_ids
        order.order_line.with_user(self.buyer).write({'price_unit': 700.0})
        self.env.flush_all()
        self.assertTrue(old_decisions.exists())
        self.assertEqual(old_decisions.revision, 1)
        self.assertEqual(old_decisions.amount, 6000.0)
