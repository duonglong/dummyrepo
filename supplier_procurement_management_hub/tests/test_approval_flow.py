"""Unit tests of T-019 beside the acceptance tests: guards on every route and edge cases.

Requirements: BR-SPM-0010, FR-SPM-0047, FR-SPM-0048, FR-SPM-0049, FR-SPM-0052, FR-SPM-0053
"""
from odoo.exceptions import AccessError, UserError

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
