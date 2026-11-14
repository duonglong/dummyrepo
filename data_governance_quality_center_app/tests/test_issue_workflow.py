from unittest.mock import patch

from odoo.exceptions import AccessError, UserError

from .acceptance_helpers import AcceptanceCase


class TestIssueWorkflow(AcceptanceCase):
    """Unit tests of the issue workflow beyond the acceptance scenarios.

    Requirements: FR-DGQ-0024, FR-DGQ-0026, FR-DGQ-0028, FR-DGQ-0030
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
        """FR-DGQ-0030 (record the user may not read): opening runs under the user's own rights, so a refused read is a message."""
        product = self.product('Other company', company=self.cy)
        issue = self.env['data.quality.issue'].create({
            'rule_id': self.rule.id, 'res_model': 'product.template', 'res_id': product.id,
            'company_id': self.cx.id, 'severity': 'warning', 'record_name': 'Other company'})
        with self.assertRaises(UserError):
            issue.with_user(self.ann).action_open_record()

    def test_a_viewer_of_the_company_can_comment(self):
        """FR-DGQ-0026: a viewer who sees the issue posts a comment and is its author."""
        viewer = self.user('viewer', login='dgq_wf_viewer', company=self.cx)
        issue = self.issue('Viewed')
        message = issue.with_user(viewer).message_post(body='asked the vendor')
        self.assertEqual(message.author_id, viewer.partner_id)

    def test_a_viewer_of_another_company_cannot_comment(self):
        """FR-DGQ-0026: whoever cannot see the issue cannot comment on it."""
        outsider = self.user('viewer', login='dgq_wf_outsider', company=self.cy)
        issue = self.issue('Hidden')
        with self.assertRaises(AccessError):
            issue.with_user(outsider).message_post(body='no')

    def test_the_notice_is_written_in_the_assignee_language(self):
        """FR-DGQ-0028: the notice follows the language of the person it is for."""
        self.env['res.lang']._activate_lang('fr_FR')
        self.ann.lang = 'fr_FR'
        issue = self.issue('Lang')
        langs = []
        template_class = type(self.env['mail.template'])
        original = template_class._render_field

        def spy(template, *args, **kwargs):
            langs.append(template.env.context.get('lang'))
            return original(template, *args, **kwargs)

        with patch.object(template_class, '_render_field', spy):
            issue.write({'user_id': self.ann.id})
        self.assertEqual(set(langs), {'fr_FR'})
