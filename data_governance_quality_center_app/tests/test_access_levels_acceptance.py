from odoo.exceptions import AccessError

from .acceptance_helpers import AcceptanceCase


class TestAccessLevelsAcceptance(AcceptanceCase):
    """Acceptance tests of the four access levels (T-041), as the server enforces them.

    Screen state (buttons hidden for a level) is left to the screen review; the server refusal,
    which the scenarios also state, is asserted here.

    Requirements: FR-DGQ-0070
    """

    def _issue(self):
        rule = self.make_rule('product.template', 'required', ['x_dgq_barcode'])
        product = self.product('Desk')
        return self.env['data.quality.issue'].create({
            'rule_id': rule.id, 'res_model': 'product.template', 'res_id': product.id,
            'company_id': self.cx.id, 'severity': 'warning', 'record_name': 'Desk'}), rule

    def test_viewer_reads_issue_but_cannot_change_status_or_assignee(self):
        """SC-DGQ-0070-1: a Viewer reads an issue; changing status or assignee is refused."""
        issue, _rule = self._issue()
        viewer = self.user('viewer', login='dgq_acc_viewer', company=self.cx)
        operator = self.user('operator', login='dgq_acc_op', company=self.cx)
        as_viewer = issue.with_user(viewer)
        self.assertEqual(as_viewer.record_name, 'Desk')
        self.assertEqual(as_viewer.state, 'new')
        with self.assertRaises(AccessError):
            as_viewer.write({'user_id': operator.id})
        with self.assertRaises(AccessError):
            as_viewer.write({'state': 'ignored', 'ignore_reason': 'x'})
        self.assertEqual(issue.state, 'new')
        self.assertFalse(issue.user_id)

    def test_viewer_cannot_create_or_delete_issue(self):
        """SC-DGQ-0070-1 (another way): a Viewer cannot create or delete an issue either."""
        issue, rule = self._issue()
        viewer = self.user('viewer', login='dgq_acc_viewer2', company=self.cx)
        with self.assertRaises(AccessError):
            issue.with_user(viewer).unlink()
        product = self.product('Chair')
        with self.assertRaises(AccessError):
            self.env['data.quality.issue'].with_user(viewer).create({
                'rule_id': rule.id, 'res_model': 'product.template', 'res_id': product.id,
                'company_id': self.cx.id, 'severity': 'warning', 'record_name': 'Chair'})

    def test_operator_reads_rules_but_cannot_create_or_change_them(self):
        """SC-DGQ-0070-2: an Operator reads rules; creating, changing or deleting one is refused."""
        _issue, rule = self._issue()
        operator = self.user('operator', login='dgq_acc_op2', company=self.cx)
        as_operator = rule.with_user(operator)
        self.assertEqual(as_operator.name, rule.name)
        with self.assertRaises(AccessError):
            as_operator.write({'name': 'Changed'})
        with self.assertRaises(AccessError):
            as_operator.unlink()
        with self.assertRaises(AccessError):
            self.env['data.quality.rule'].with_user(operator).create({
                'name': 'New', 'model_id': self.model('product.template').id,
                'rule_type': 'required', 'severity': 'warning'})
        self.assertNotEqual(rule.name, 'Changed')

    def test_manager_can_change_rules(self):
        """SC-DGQ-0070-2 (contrast): the next level, Manager, may change a rule."""
        _issue, rule = self._issue()
        manager = self.user('manager', login='dgq_acc_mgr', company=self.cx)
        rule.with_user(manager).write({'name': 'Renamed by manager'})
        self.assertEqual(rule.name, 'Renamed by manager')
