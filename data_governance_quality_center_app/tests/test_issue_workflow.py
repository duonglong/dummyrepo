from odoo.exceptions import UserError

from .acceptance_helpers import AcceptanceCase


class TestIssueWorkflow(AcceptanceCase):
    """Unit tests of the issue workflow beyond the acceptance scenarios.

    Requirements: FR-DGQ-0024, FR-DGQ-0028, FR-DGQ-0030
    """

    def setUp(self):
        super().setUp()
        self.ann = self.user('operator', login='dgq_wf_ann', company=self.cx)
        self.bob = self.user('operator', login='dgq_wf_bob', company=self.cx)
        self.ann.write({'email': 'ann-wf@example.com', 'notification_type': 'inbox'})
        self.bob.write({'email': 'bob-wf@example.com', 'notification_type': 'inbox'})
        self.env['res.config.settings'].create({'dgq_notify_email': True}).execute()
        self.rule = self.make_rule('product.template', 'required', ['x_dgq_barcode'])

    def issue(self, name):
        product = self.product(name)
        return self.env['data.quality.issue'].create({
            'rule_id': self.rule.id, 'res_model': 'product.template', 'res_id': product.id,
            'company_id': self.cx.id, 'severity': 'warning', 'record_name': name})

    def mails_to(self, user):
        return self.env['mail.mail'].search([('email_to', 'ilike', user.email)])

    def test_bulk_assignment_sends_one_notice_per_user(self):
        """FR-DGQ-0028: issues for two users give each user one e-mail."""
        issues = self.issue('A1') | self.issue('A2') | self.issue('B1')
        issues[:2].write({'user_id': self.ann.id})
        issues[2].write({'user_id': self.bob.id})
        self.assertEqual(len(self.mails_to(self.ann)), 1)
        self.assertEqual(len(self.mails_to(self.bob)), 1)

    def test_reassigning_to_the_same_user_sends_nothing(self):
        """FR-DGQ-0028: only issues whose assignee changed are announced."""
        issue = self.issue('Same')
        issue.write({'user_id': self.ann.id})
        issue.write({'user_id': self.ann.id, 'priority': 'high'})
        self.assertEqual(len(self.mails_to(self.ann)), 1)

    def test_unassigning_needs_no_check_and_sends_nothing(self):
        """FR-DGQ-0024: removing the assignee returns the issue to New without a notice."""
        issue = self.issue('Back')
        issue.write({'user_id': self.ann.id})
        issue.write({'user_id': False})
        self.assertEqual(issue.state, 'new')
        self.assertEqual(len(self.mails_to(self.ann)), 1)

    def test_a_user_who_may_not_read_the_record_gets_a_message(self):
        """SC-DGQ-0030-1: opening runs under the user's own rights, so a refused read is a message."""
        product = self.product('Other company', company=self.cy)
        issue = self.env['data.quality.issue'].create({
            'rule_id': self.rule.id, 'res_model': 'product.template', 'res_id': product.id,
            'company_id': self.cx.id, 'severity': 'warning', 'record_name': 'Other company'})
        with self.assertRaises(UserError):
            issue.with_user(self.ann).action_open_record()
