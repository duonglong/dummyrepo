"""Unit tests of T-019 beside the acceptance tests: guards on every route and edge cases.

Requirements: BR-SPM-0010, FR-SPM-0047, FR-SPM-0048, FR-SPM-0049, FR-SPM-0052, FR-SPM-0053
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form

from ..services import approval
from .approval_common import SpmApprovalCase


class TestApprovalFlow(SpmApprovalCase):
    """Requirements: FR-SPM-0047, FR-SPM-0048, FR-SPM-0049, FR-SPM-0052, FR-SPM-0053"""

    def _pending_order(self):
        self._policy('Amount', steps=[self._step('Bob', 10, users=self.bob)], amount_min=5000.0)
        order = self._order()
        self._submit(order)
        return order

    def test_the_approval_fields_are_not_set_by_a_buyer(self):
        """FR-SPM-0048: a Buyer cannot mark an order approved on create or write."""
        order = self._order()
        with self.assertRaises(AccessError):
            order.with_user(self.buyer).write({'spm_approval_state': 'approved'})
        with self.assertRaises(AccessError):
            self.env['purchase.order'].with_user(self.buyer).create({
                'partner_id': self.acme.id, 'spm_controlled': False, 'spm_revision': 5})

    def test_a_controlled_order_is_guarded_even_for_the_superuser(self):
        """FR-SPM-0048: writing the confirmed state on a pending order is refused with elevated rights."""
        order = self._pending_order()
        with self.assertRaises(UserError):
            order.sudo().write({'state': 'purchase'})  # sudo: proves the guard holds for elevated rights too
        with self.assertRaises(UserError):
            order.sudo().button_approve(force=True)  # sudo: proves the guard holds for elevated rights too

    def test_a_price_and_a_new_line_in_one_save_make_one_revision(self):
        """FR-SPM-0052: one save with several material changes re-evaluates once."""
        self._amount_order = self._pending_order()
        order = self._amount_order
        line = order.order_line
        order.with_user(self.buyer).write({'order_line': [
            (1, line.id, {'price_unit': 700.0}),
            (0, 0, {'product_id': self.chair.id, 'name': 'Extra', 'product_qty': 1.0,
                    'price_unit': 10.0, 'tax_ids': [(6, 0, [])]})]})
        self.env.flush_all()
        self.assertEqual(order.spm_revision, 2)
        self.assertEqual(len(order.spm_approval_request_ids), 2)
        self.assertEqual(self._request(order).snapshot_amount, 7010.0)

    def test_a_section_line_is_not_material(self):
        """BR-SPM-0010: a section carries no product or price."""
        order = self._pending_order()
        order.with_user(self.buyer).write({'order_line': [(0, 0, {
            'display_type': 'line_section', 'name': 'Section', 'product_qty': 0.0})]})
        self.assertEqual(order.spm_revision, 1)
        self.assertEqual(self._request(order).state, 'pending')

    def test_a_resubmission_after_rejection_is_a_new_revision(self):
        """FR-SPM-0049: the order resubmitted after a rejection is revision 2."""
        order = self._pending_order()
        self._request(order).step_ids.with_user(self.bob).action_spm_reject('No')
        self._submit(order)
        self.assertEqual(order.spm_revision, 2)
        self.assertEqual(self._request(order).revision, 2)

    def test_an_order_to_approve_cannot_be_submitted_twice(self):
        """FR-SPM-0047: a second submission of a pending order is refused."""
        order = self._pending_order()
        with self.assertRaises(UserError):
            order.with_user(self.buyer).action_spm_submit_approval()

    def test_an_approver_decides_once_on_an_all_step(self):
        """FR-SPM-0049: Bob cannot approve the same step of mode all twice."""
        self._policy('Both', steps=[self._step(
            'Both', 10, users=self.bob | self.carla, mode='all')], amount_min=5000.0)
        step = self._submit(self._order()).step_ids
        step.with_user(self.bob).action_spm_approve()
        with self.assertRaises(UserError):
            step.with_user(self.bob).action_spm_approve()
        self.assertEqual(len(step.decision_ids), 1)

    def test_a_decision_is_written_to_the_audit_trail(self):
        """FR-SPM-0053: the decision has an audit entry with the revision."""
        request = self._pending_order().spm_approval_request_ids
        request.step_ids.with_user(self.bob).action_spm_approve('ok')
        decision = request.step_ids.decision_ids
        entry = self.env['procurement.audit.log'].search([
            ('res_model', '=', 'procurement.approval.decision'), ('res_id', '=', decision.id)])
        self.assertEqual(entry.event, 'approval')
        self.assertEqual(entry.new_values['revision'], 1)
        self.assertEqual(entry.user_id, self.bob)

    def test_the_reject_dialog_rejects_with_its_reason(self):
        """FR-SPM-0049: the dialog of the Reject button passes the reason to the step."""
        request = self._pending_order().spm_approval_request_ids
        wizard = self.env['procurement.approval.reject.wizard'].with_user(self.bob).create({
            'step_id': request.step_ids.id, 'reason': 'Too dear'})
        wizard.action_confirm()
        self.assertEqual(request.state, 'rejected')

    def test_a_product_swap_at_the_same_price_is_material_in_a_second_save(self):
        """BR-SPM-0010: a first non-material save must not hide the change of a later save of the same transaction."""
        order = self._pending_order()
        line = order.order_line
        order.with_user(self.buyer).write({'origin': 'first save'})
        order.with_user(self.buyer).write({'order_line': [(1, line.id, {
            'product_id': self.chair.id, 'price_unit': line.price_unit})]})
        self.assertEqual(order.spm_revision, 1)  # the same product: nothing changed
        order.with_user(self.buyer).write({'order_line': [(1, line.id, {
            'product_id': self.laptop.id, 'price_unit': line.price_unit})]})
        self.assertEqual(order.spm_revision, 2)

    def test_two_orders_saved_one_after_the_other_are_each_reviewed(self):
        """BR-SPM-0010: the structural change of the second order is seen, not lost with the first save."""
        first = self._pending_order()
        second = self._order(partner=self.beta)
        self._submit(second)
        first.with_user(self.buyer).write({'origin': 'first'})
        second.with_user(self.buyer).write({'order_line': [(1, second.order_line.id, {
            'product_id': self.laptop.id, 'price_unit': second.order_line.price_unit})]})
        self.assertEqual((first.spm_revision, second.spm_revision), (1, 2))

    def test_a_removed_line_and_an_analytic_change_are_material(self):
        """BR-SPM-0010: a removed line (the total goes down) and a changed analytic distribution re-run the approval."""
        order = self._pending_order()
        order.with_user(self.buyer).write({'order_line': [(0, 0, {
            'product_id': self.cable.id, 'name': 'Cable', 'product_qty': 1.0,
            'price_unit': 0.0, 'tax_ids': [(6, 0, [])]})]})
        self.assertEqual(order.spm_revision, 2)
        order.with_user(self.buyer).write({'order_line': [(2, order.order_line[-1].id)]})
        self.assertEqual(order.spm_revision, 3)
        account = self.env['account.analytic.account'].create({
            'name': 'Project M3', 'plan_id': self.env['account.analytic.plan'].create({'name': 'Plan M3'}).id})
        order.with_user(self.buyer).write({'order_line': [(1, order.order_line.id, {
            'analytic_distribution': {str(account.id): 100.0}})]})
        self.assertEqual(order.spm_revision, 4)

    def test_a_payment_term_change_is_material(self):
        """BR-SPM-0010: the payment terms are part of what was approved."""
        order = self._pending_order()
        order.with_user(self.buyer).write({'payment_term_id': self.env.ref('account.account_payment_term_30days').id})
        self.assertEqual(order.spm_revision, 2)

    def test_pending_orders_of_the_hub_count_in_the_cumulative_total(self):
        """FR-SPM-0047: an 8,000 order pending hub approval plus a new 3,000 order opens the cumulative policy."""
        self._policy('Pending', steps=[self._step('Eve', 10, users=self.eve)], amount_min=10000.0,
                     partner_ids=self.acme, cumulative_period='month')
        self._policy('Big', sequence=20, steps=[self._step('Bob', 10, users=self.bob)], amount_min=7000.0)
        pending = self._order(price=8000.0, qty=1.0)
        self._submit(pending)
        small = self._order(price=3000.0, qty=1.0)
        request = self._submit(small)
        self.assertEqual(request.cumulated_amount, 11000.0)
        self.assertEqual(request.step_ids.approver_ids, self.eve)

    def test_the_requester_of_the_linked_requests_is_the_requester_of_the_order(self):
        """FR-SPM-0047: the single requester and department of the purchase requests, the buyer when they differ."""
        ann, _bob, _carla = self._employees()
        department = self.env['hr.department'].create({'name': 'Dept M3'})
        ann.department_id = department
        request = self.env['procurement.request'].create({
            'requester_id': self.ann.id, 'department_id': department.id,
            'needed_date': '2030-01-01', 'reason': 'Need'})
        order = self._order()
        order.spm_request_ids = request
        self.assertEqual(approval.order_requester(order), self.ann)
        self.assertEqual(approval.requester_department(order), department)
        other = self.env['procurement.request'].create({
            'requester_id': self.dave.id, 'needed_date': '2030-01-01', 'reason': 'Need'})
        order.spm_request_ids = request | other
        self.assertEqual(approval.order_requester(order), self.buyer)
        order.spm_requester_id = self.carla
        self.assertEqual(approval.order_requester(order), self.carla)

    def test_a_step_of_a_company_the_user_cannot_work_in_is_refused(self):
        """FR-SPM-0049: an approver cannot decide on a step of a company outside his allowed companies."""
        step = self._pending_order().spm_approval_request_ids.step_ids
        other = self.env['res.company'].create({'name': 'Other Co T019'})
        self.env.cr.execute(
            "UPDATE procurement_approval_step SET company_id = %s WHERE id = %s", (other.id, step.id))
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            step.with_user(self.bob).action_spm_approve()

    def test_the_approve_dialog_passes_the_comment_to_the_step(self):
        """FR-SPM-0049: the comment typed in the dialog of the Approve button is the decision's comment.

        Form fills and saves the dialog; the button itself is left to the screen review.
        """
        step = self._pending_order().spm_approval_request_ids.step_ids
        action = step.with_user(self.bob).action_spm_open_approve_wizard()
        with Form(self.env['procurement.approval.approve.wizard'].with_user(self.bob).with_context(
                **action['context'])) as form:
            form.comment = 'Fine by me'
        form.record.action_confirm()
        self.assertEqual(step.decision_ids.comment, 'Fine by me')
