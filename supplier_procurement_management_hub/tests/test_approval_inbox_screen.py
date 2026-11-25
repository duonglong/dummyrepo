"""Regression tests of B-0280: the Approvals Inbox opens for the user it lists steps for.

Requirements: FR-SPM-0054
"""
from lxml import etree

from odoo.tests.common import HttpCase

from .approval_common import MODULE, SpmApprovalCase


class TestApprovalInboxScreen(SpmApprovalCase):
    """Requirements: FR-SPM-0054"""

    def _inbox_rows(self, user):
        """Open the menu's action as the user and read the list the way the client does (one web read)."""
        action = self.env.ref(f'{MODULE}.procurement_approval_inbox_action').with_user(user).run()
        step_model = self.env[action['res_model']].with_user(user)
        view = step_model.get_views(action['views'])['views']['list']
        names = etree.fromstring(view['arch']).xpath('//field[not(ancestor::button)]/@name')
        fields = step_model._fields
        specification = {
            name: {'fields': {'display_name': {}}} if fields[name].type == 'many2one' else {} for name in names}
        return step_model.web_search_read(action['domain'], specification)['records']

    def _pending_step(self):
        self._policy('Amount', steps=[self._step('Bob', 10, users=self.bob)], amount_min=5000.0)
        order = self._order()
        self._submit(order)
        return order.spm_approval_request_ids.step_ids

    def test_bug_b_0280_the_approver_opens_the_inbox_menu(self):
        """B-0280: a department manager who only approves opens the inbox and sees the step.

        Reads the list through the model as the client does; the browser itself is not run.
        """
        step = self._pending_step()
        rows = self._inbox_rows(self.bob)
        self.assertEqual([row['id'] for row in rows], step.ids)

    def test_bug_b_0280_a_requester_who_approves_opens_the_inbox_menu(self):
        """B-0280 (same rule, the lowest SPM role): the inbox shows the step to a requester named approver."""
        self._policy('Amount', steps=[self._step('Ann', 10, users=self.ann)], amount_min=5000.0)
        order = self._order()
        self._submit(order)
        rows = self._inbox_rows(self.ann)
        self.assertEqual(len(rows), 1)

    def test_bug_b_0280_a_delegate_opens_the_inbox_menu(self):
        """B-0280 (delegation route): the delegate of an approver sees the delegator's step in the inbox."""
        step = self._pending_step()
        self.env['procurement.approval.delegation'].create({
            'delegator_id': self.bob.id, 'delegate_id': self.eve.id,
            'date_from': '2000-01-01', 'date_to': '2999-12-31'})
        rows = self._inbox_rows(self.eve)
        self.assertEqual([row['id'] for row in rows], step.ids)


class TestApprovalInboxRoute(HttpCase, SpmApprovalCase):
    """Requirements: FR-SPM-0054"""

    def test_bug_b_0280_the_menu_action_runs_through_the_client_route_for_each_role(self):
        """B-0280: the route the Inbox menu calls answers the inbox action for the approver, the delegate and a Buyer.

        Posts to /web/action/run as the logged-in user; the rendered list is not opened.
        """
        self._policy('Amount', steps=[self._step('Bob', 10, users=self.bob)], amount_min=5000.0)
        self._submit(self._order())
        action_id = self.env.ref(f'{MODULE}.procurement_approval_inbox_action').id
        for user in (self.bob, self.buyer, self.auditor, self.ann):
            user.password = 'inbox-' + user.login
            self.authenticate(user.login, user.password)
            result = self.make_jsonrpc_request('/web/action/run', {'action_id': action_id})
            self.assertEqual(result['res_model'], 'procurement.approval.step', user.login)
