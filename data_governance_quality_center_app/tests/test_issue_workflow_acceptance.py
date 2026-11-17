import re

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError, UserError

from .acceptance_helpers import AcceptanceCase

DEADLINE = '2026-11-20'


class TestIssueWorkflowAcceptance(AcceptanceCase):
    """Acceptance tests of T-012: assign, move through statuses, history, notify, open the record.

    The assignment is a write of the assignee, priority and deadline on the issue, as the form does;
    a bulk assignment is one write on several issues. The bulk dialog (``data.quality.issue.bulk``)
    is not driven here: the design does not name the method of its button, so the dialog step is
    left to the build. Tracking is switched on for these tests (history is Odoo's own tracking).

    Requirements: FR-DGQ-0024, FR-DGQ-0025, FR-DGQ-0026, FR-DGQ-0028, FR-DGQ-0030
    """

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=False))
        self.ann = self.user('operator', login='dgq_m2_ann', company=self.cx)
        self.op = self.user('operator', login='dgq_m2_op', company=self.cx)
        self.ann.write({'email': 'ann@example.com', 'notification_type': 'inbox'})
        self.op.write({'email': 'op@example.com'})
        self.rule = self.make_rule('product.template', 'required', ['x_dgq_barcode'])
        self.Issue = self.env['data.quality.issue']

    def issue(self, name='Desk'):
        product = self.product(name)
        return self.Issue.create({
            'rule_id': self.rule.id, 'res_model': 'product.template', 'res_id': product.id,
            'company_id': self.cx.id, 'severity': 'warning', 'record_name': name})

    def label(self, state):
        return dict(self.Issue._fields['state'].selection)[state]

    # ---- FR-DGQ-0024 -------------------------------------------------------

    def test_operator_assigns_with_priority_and_deadline(self):
        """SC-DGQ-0024-1: the issue is Assigned to Ann with priority urgent and deadline 2026-11-20."""
        issue = self.issue()
        self.assertEqual(issue.state, 'new')
        issue.with_user(self.op).write({'user_id': self.ann.id, 'priority': 'urgent', 'deadline': DEADLINE})
        self.assertEqual(issue.state, 'assigned')
        self.assertEqual(issue.user_id, self.ann)
        self.assertEqual(issue.priority, 'urgent')
        self.assertEqual(str(issue.deadline), DEADLINE)

    def test_priority_and_deadline_are_optional(self):
        """FR-DGQ-0024: an assignment alone keeps the priority the severity gave."""
        issue = self.issue()
        before = issue.priority
        issue.with_user(self.op).write({'user_id': self.ann.id})
        self.assertEqual(issue.state, 'assigned')
        self.assertEqual(issue.priority, before)

    def test_user_without_access_to_the_company_is_refused(self):
        """SC-DGQ-0024-2: a user who cannot see the issue's company cannot be assigned."""
        outsider = self.user('operator', login='dgq_m2_out', company=self.cy)
        outsider.write({'company_ids': [Command.set([self.cy.id])], 'company_id': self.cy.id})
        issue = self.issue()
        with self.assertRaises(UserError) as caught:
            issue.with_user(self.op).write({'user_id': outsider.id})
        self.assertIn('company', str(caught.exception).lower())
        self.assertEqual(issue.state, 'new')
        self.assertFalse(issue.user_id)

    def test_viewer_cannot_be_assigned(self):
        """FR-DGQ-0024: only users with at least operator access can be assigned."""
        viewer = self.user('viewer', login='dgq_m2_view', company=self.cx)
        issue = self.issue()
        with self.assertRaises(UserError):
            issue.with_user(self.op).write({'user_id': viewer.id})

    # ---- FR-DGQ-0025 -------------------------------------------------------

    def test_assignee_moves_the_issue_to_resolved(self):
        """SC-DGQ-0025-1: Ann sets her Assigned issue In Progress and then Resolved."""
        issue = self.issue()
        issue.write({'user_id': self.ann.id})
        as_ann = issue.with_user(self.ann)
        as_ann.write({'state': 'in_progress'})
        self.assertEqual(issue.state, 'in_progress')
        as_ann.write({'state': 'resolved'})
        self.assertEqual(issue.state, 'resolved')

    def test_ignoring_needs_a_reason(self):
        """SC-DGQ-0025-2: Ignored without a reason is refused with a message that a reason is needed."""
        issue = self.issue()
        issue.write({'user_id': self.ann.id})
        with self.assertRaises(UserError) as caught:
            issue.with_user(self.ann).write({'state': 'ignored'})
        self.assertIn('reason', str(caught.exception).lower())
        self.assertEqual(issue.state, 'assigned')
        issue.with_user(self.ann).write({'state': 'ignored', 'ignore_reason': 'Not a problem for us'})
        self.assertEqual(issue.state, 'ignored')

    def test_resolved_issue_must_be_reopened_first(self):
        """SC-DGQ-0025-3: Resolved to In Progress is refused with a message that it must be reopened first."""
        issue = self.issue()
        issue.write({'state': 'resolved'})
        with self.assertRaises(UserError) as caught:
            issue.with_user(self.ann).write({'state': 'in_progress'})
        self.assertIn('reopen', str(caught.exception).lower())
        self.assertEqual(issue.state, 'resolved')

    def test_refusal_names_the_allowed_statuses(self):
        """FR-DGQ-0025: a refused change names the statuses that are allowed from where the issue is."""
        issue = self.issue()
        issue.write({'state': 'in_progress'})
        with self.assertRaises(UserError) as caught:
            issue.with_user(self.ann).write({'state': 'new'})
        message = str(caught.exception)
        for allowed in ('assigned', 'resolved', 'ignored'):
            self.assertIn(self.label(allowed), message)

    # ---- FR-DGQ-0026 -------------------------------------------------------

    def test_history_and_comment_keep_who_and_when(self):
        """SC-DGQ-0026-1: the assignment (operator), the status change and the comment (Ann), each with a time."""
        issue = self.issue()
        issue.with_user(self.op).write({'user_id': self.ann.id, 'priority': 'high', 'deadline': DEADLINE})
        issue.with_user(self.ann).write({'state': 'in_progress'})
        issue.with_user(self.ann).message_post(body='asked the vendor')
        self.env.flush_all()
        tracked = {}
        for message in issue.message_ids:
            for value in message.tracking_value_ids:
                tracked.setdefault(value.field_id.name, []).append(message)
        self.assertIn('user_id', tracked)
        self.assertIn('state', tracked)
        self.assertIn('priority', tracked)
        self.assertIn('deadline', tracked)
        self.assertEqual(tracked['user_id'][0].author_id, self.op.partner_id)
        in_progress = [m for m in tracked['state'] if m.author_id == self.ann.partner_id]
        self.assertTrue(in_progress)
        comment = issue.message_ids.filtered(lambda m: 'asked the vendor' in (m.body or ''))
        self.assertEqual(len(comment), 1)
        self.assertEqual(comment.author_id, self.ann.partner_id)
        for message in tracked['user_id'] + in_progress + [comment]:
            self.assertTrue(message.date)

    def test_history_lines_cannot_be_edited_or_deleted_by_users(self):
        """FR-DGQ-0026: an ordinary user cannot edit or delete a tracked change."""
        issue = self.issue()
        issue.with_user(self.op).write({'user_id': self.ann.id})
        self.env.flush_all()
        values = issue.message_ids.tracking_value_ids
        self.assertTrue(values)
        with self.assertRaises(AccessError):
            values.with_user(self.ann).write({'new_value_char': 'forged'})
        with self.assertRaises(AccessError):
            values.with_user(self.ann).unlink()

    # ---- FR-DGQ-0028 -------------------------------------------------------

    def _notify_by_email(self, on):
        self.env['res.config.settings'].create({'dgq_notify_email': on}).execute()

    def _mails_to(self, user):
        return self.env['mail.mail'].search([
            '|', ('recipient_ids', 'in', user.partner_id.id), ('email_to', 'ilike', user.email)])

    def _notifications_to(self, user):
        return self.env['mail.notification'].search([
            ('res_partner_id', '=', user.partner_id.id),
            ('mail_message_id.model', '=', 'data.quality.issue')])

    def test_bulk_assignment_sends_one_email_listing_the_issues(self):
        """SC-DGQ-0028-1: assigning 30 issues to Ann in one action gives her one e-mail listing all 30.

        The one action is one write on the 30 issues; the bulk dialog's button is left to the build.
        """
        self._notify_by_email(True)
        issues = self.Issue
        for number in range(30):
            issues |= self.issue('Listed-%02d' % number)
        before = self._mails_to(self.ann)
        issues.with_user(self.op).write({'user_id': self.ann.id})
        self.env.flush_all()
        mails = self._mails_to(self.ann) - before
        self.assertEqual(len(mails), 1)
        body = mails.body_html or ''
        for issue in issues:
            self.assertTrue(issue.record_name in body or issue.name in body, issue.record_name)

    def test_assignment_notifies_in_odoo_and_by_email_only_when_the_policy_says(self):
        """FR-DGQ-0028: Ann is notified in Odoo; the e-mail follows the notification policy."""
        self._notify_by_email(False)
        issue = self.issue('Quiet')
        mails = self._mails_to(self.ann)
        issue.with_user(self.op).write({'user_id': self.ann.id, 'deadline': DEADLINE})
        self.env.flush_all()
        self.assertTrue(self._notifications_to(self.ann))
        self.assertEqual(self._mails_to(self.ann), mails)

    def test_self_assignment_sends_nothing(self):
        """SC-DGQ-0028-2: Ann assigns an issue to herself and nobody is notified."""
        self._notify_by_email(True)
        issue = self.issue('Mine')
        mails = self._mails_to(self.ann)
        notified = self._notifications_to(self.ann)
        issue.with_user(self.ann).write({'user_id': self.ann.id})
        self.env.flush_all()
        self.assertEqual(issue.user_id, self.ann)
        self.assertEqual(self._mails_to(self.ann), mails)
        self.assertEqual(self._notifications_to(self.ann), notified)

    # ---- FR-DGQ-0030 -------------------------------------------------------

    def _open_record(self, issue):
        """Click the issue form's 'open the record' button, as the user: an action, or a message."""
        arch = self.Issue.get_view(view_type='form')['arch']
        buttons = [
            button.get('name') for button in etree.fromstring(arch).iter('button')
            if button.get('type') == 'object' and button.get('name')
            and re.search(r'open.*record|record.*open', button.get('name'))]
        self.assertTrue(buttons, 'the issue form has no button that opens the record')
        try:
            return getattr(issue, buttons[0])()
        except UserError as error:
            return {'message': str(error)}

    def test_opening_the_record_shows_its_own_form(self):
        """SC-DGQ-0030-1: the link of an issue on product 'Desk' opens the product form of 'Desk'."""
        issue = self.issue('Desk')
        action = self._open_record(issue.with_user(self.ann))
        self.assertEqual(action.get('type'), 'ir.actions.act_window')
        self.assertEqual(action.get('res_model'), 'product.template')
        self.assertEqual(action.get('res_id'), issue.res_id)
        self.assertIn('form', action.get('view_mode', 'form'))

    def test_opening_a_deleted_record_shows_a_message(self):
        """SC-DGQ-0030-2: for a contact deleted since, a message says the record no longer exists."""
        contact = self.partner('Gone contact')
        rule = self.make_rule('res.partner', 'required', ['ref'])
        issue = self.Issue.create({
            'rule_id': rule.id, 'res_model': 'res.partner', 'res_id': contact.id,
            'company_id': self.cx.id, 'severity': 'warning', 'record_name': 'Gone contact'})
        contact.unlink()
        result = self._open_record(issue.with_user(self.ann))
        self.assertNotEqual(result.get('type'), 'ir.actions.act_window')
        self.assertIn('exist', str(result).lower())
