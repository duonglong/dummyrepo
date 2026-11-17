import re

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import Form

from .acceptance_m2b_helpers import BULK_APPLY, M2BCase, call_first


class TestIssueBulkAcceptance(M2BCase):
    """Acceptance tests of T-013: bulk issue actions and resolution time.

    The bulk dialog (``data.quality.issue.bulk``) is driven with ``Form`` in the action's context:
    the selected issues come as ``default_issue_ids``, the action and its values are set, the
    dialog is saved and its button is called (the design does not name the button's method, so the
    usual names are tried). The dialog must carry the selected issues in its view, as the list
    action's context hands them over. The analysis screen (average resolution time per rule, model,
    company and assignee) is left to the build: the design names no analysis model.

    Requirements: FR-DGQ-0027, FR-DGQ-0029
    """

    def setUp(self):
        super().setUp()
        self.ann = self.user('operator', login='dgq_m2b_ann_op', company=self.cx)
        self.op = self.user('operator', login='dgq_m2b_op', company=self.cx)
        self.rule = self.make_rule('product.template', 'required', ['x_dgq_barcode'])
        self.Issue = self.env['data.quality.issue']
        self.counter = 0

    def issue(self, state='new', name=None):
        self.counter += 1
        name = name or 'Item-%03d' % self.counter
        product = self.product(name)
        issue = self.Issue.create({
            'rule_id': self.rule.id, 'res_model': 'product.template', 'res_id': product.id,
            'company_id': self.cx.id, 'severity': 'warning', 'record_name': name})
        if state != 'new':
            issue.write({'user_id': self.ann.id})
            if state == 'in_progress':
                issue.write({'state': 'in_progress'})
            elif state == 'ignored':
                issue.write({'state': 'ignored', 'ignore_reason': 'Accepted as it is'})
        return issue

    def bulk(self, issues, user, action, **values):
        """Open the bulk dialog on the issues as ``user``, set the action, save and press the button."""
        Bulk = self.env['data.quality.issue.bulk'].with_user(user)
        context = {
            'active_model': 'data.quality.issue', 'active_ids': issues.ids,
            'default_issue_ids': [Command.set(issues.ids)],
        }
        with Form(Bulk.with_context(**context)) as form:
            form.action = action
            for name, value in values.items():
                setattr(form, name, value)
        wizard = form.record
        call_first(wizard, BULK_APPLY)
        wizard.invalidate_recordset()
        return wizard

    # ---- FR-DGQ-0027 -------------------------------------------------------

    def test_fifty_new_issues_are_assigned_in_one_action(self):
        """SC-DGQ-0027-1: 50 New issues assigned to Ann in one action are Assigned and the result says 50."""
        issues = self.Issue
        for _ in range(50):
            issues |= self.issue()
        wizard = self.bulk(issues, self.op, 'assign', user_id=self.ann)
        issues.invalidate_recordset()
        self.assertEqual(set(issues.mapped('state')), {'assigned'})
        self.assertEqual(issues.mapped('user_id'), self.ann)
        self.assertTrue(re.search(r'\b50\b', wizard.result_message or ''), wizard.result_message)

    def test_resolving_skips_ignored_issues_and_names_them(self):
        """SC-DGQ-0027-2: 10 In Progress and 2 Ignored issues resolved: 10 are Resolved, 2 are listed as skipped."""
        working = self.Issue
        for _ in range(10):
            working |= self.issue('in_progress')
        ignored = self.issue('ignored', 'Ignored-One') | self.issue('ignored', 'Ignored-Two')
        wizard = self.bulk(working | ignored, self.op, 'resolve')
        (working | ignored).invalidate_recordset()
        self.assertEqual(set(working.mapped('state')), {'resolved'})
        self.assertEqual(set(ignored.mapped('state')), {'ignored'})
        message = wizard.result_message or ''
        self.assertTrue(re.search(r'\b10\b', message), message)
        self.assertIn('Ignored-One', message)
        self.assertIn('Ignored-Two', message)
        self.assertIn('reopen', message.lower())

    def test_priority_and_deadline_apply_to_every_issue(self):
        """FR-DGQ-0027: the priority and then the deadline of selected issues are set in one action each."""
        issues = self.issue() | self.issue() | self.issue('in_progress')
        self.bulk(issues, self.op, 'priority', priority='urgent')
        issues.invalidate_recordset()
        self.assertEqual(set(issues.mapped('priority')), {'urgent'})
        self.bulk(issues, self.op, 'deadline', deadline='2026-12-01')
        issues.invalidate_recordset()
        self.assertEqual({str(day) for day in issues.mapped('deadline')}, {'2026-12-01'})

    def test_issues_that_cannot_change_do_not_stop_the_others(self):
        """FR-DGQ-0027: a selection with an Ignored issue still assigns the others and reports 1 skipped."""
        movable = self.issue() | self.issue()
        ignored = self.issue('ignored', 'Stays-Ignored')
        wizard = self.bulk(movable | ignored, self.op, 'assign', user_id=self.op)
        (movable | ignored).invalidate_recordset()
        self.assertEqual(set(movable.mapped('state')), {'assigned'})
        self.assertEqual(ignored.state, 'ignored')
        self.assertIn('Stays-Ignored', wizard.result_message or '')

    def test_viewer_cannot_use_the_bulk_dialog(self):
        """FR-DGQ-0027: only an operator or above changes issues in bulk."""
        viewer = self.user('viewer', login='dgq_m2b_viewer', company=self.cx)
        issues = self.issue() | self.issue()
        with self.assertRaises(AccessError):
            self.bulk(issues, viewer, 'assign', user_id=self.ann)
        self.assertEqual(set(issues.mapped('state')), {'new'})

    # ---- FR-DGQ-0029 -------------------------------------------------------

    def test_resolution_time_counts_from_the_first_finding(self):
        """SC-DGQ-0029-1: first found 2026-11-10 08:00, resolved 2026-11-12 08:00 gives 48 hours."""
        issue = self.issue('in_progress')
        issue.write({'first_found': '2026-11-10 08:00:00'})
        with self.mock_datetime_and_now('2026-11-12 08:00:00'):
            issue.write({'state': 'resolved'})
        self.env.flush_all()
        issue.invalidate_recordset()
        self.assertAlmostEqual(issue.resolution_hours, 48.0, places=2)
        self.assertEqual(str(issue.date_resolved), '2026-11-12 08:00:00')

    def test_resolution_time_is_in_calendar_hours(self):
        """FR-DGQ-0029: 36 hours between the finding and the resolution are 36 hours, not working hours."""
        issue = self.issue()
        issue.write({'first_found': '2026-11-13 20:00:00'})
        with self.mock_datetime_and_now('2026-11-15 08:00:00'):
            issue.write({'state': 'resolved'})
        self.env.flush_all()
        issue.invalidate_recordset()
        self.assertAlmostEqual(issue.resolution_hours, 36.0, places=2)

    def test_bulk_resolution_stores_the_resolution_time_too(self):
        """FR-DGQ-0029 with FR-DGQ-0027: an issue resolved by the bulk dialog gets its date and hours."""
        issue = self.issue('in_progress')
        issue.write({'first_found': '2026-11-10 08:00:00'})
        with self.mock_datetime_and_now('2026-11-11 20:00:00'):
            self.bulk(issue, self.op, 'resolve')
        self.env.flush_all()
        issue.invalidate_recordset()
        self.assertEqual(issue.state, 'resolved')
        self.assertAlmostEqual(issue.resolution_hours, 36.0, places=2)
        self.assertTrue(issue.date_resolved)
