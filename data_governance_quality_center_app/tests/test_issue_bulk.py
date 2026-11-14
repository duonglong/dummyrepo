from odoo.exceptions import UserError
from odoo.tests import Form

from .acceptance_m2b_helpers import M2BCase


class TestIssueBulk(M2BCase):
    """Unit tests of the bulk dialog beyond the acceptance scenarios.

    The dialog is opened with ``Form`` and saved; the Apply button is not pressed by ``Form``,
    so its method is called and the button itself is left to the screen review.

    Requirements: FR-DGQ-0027, FR-DGQ-0029
    """

    def setUp(self):
        super().setUp()
        self.op = self.user('operator', login='dgq_m2b_bulk_op', company=self.cx)
        self.rule = self.make_rule('product.template', 'required', ['x_dgq_barcode'])
        self.Issue = self.env['data.quality.issue']

    def issue(self, name):
        product = self.product(name)
        return self.Issue.create({
            'rule_id': self.rule.id, 'res_model': 'product.template', 'res_id': product.id,
            'company_id': self.cx.id, 'severity': 'warning', 'record_name': name})

    def apply(self, issues, **values):
        Bulk = self.env['data.quality.issue.bulk'].with_user(self.op)
        with Form(Bulk.with_context(default_issue_ids=issues.ids)) as form:
            for name, value in values.items():
                setattr(form, name, value)
        wizard = form.record
        wizard.action_apply()
        return wizard

    def test_ignoring_keeps_the_reason_and_reopening_returns_to_new(self):
        """FR-DGQ-0027: ignore with a reason, then reopen the ignored issues."""
        issues = self.issue('Bulk-A') | self.issue('Bulk-B')
        self.apply(issues, action='ignore', reason='Accepted as it is')
        self.assertEqual(set(issues.mapped('state')), {'ignored'})
        self.assertEqual(set(issues.mapped('ignore_reason')), {'Accepted as it is'})
        wizard = self.apply(issues, action='reopen')
        self.assertEqual(set(issues.mapped('state')), {'new'})
        self.assertIn('2', wizard.result_message)

    def test_ignoring_without_a_reason_is_refused(self):
        """FR-DGQ-0027: the dialog refuses to ignore with no reason."""
        issues = self.issue('Bulk-C')
        wizard = self.env['data.quality.issue.bulk'].with_user(self.op).create({
            'issue_ids': issues.ids, 'action': 'ignore'})
        with self.assertRaises(UserError):
            wizard.action_apply()
        self.assertEqual(issues.state, 'new')

    def test_reopening_an_open_issue_is_skipped(self):
        """FR-DGQ-0027: reopen changes only resolved or ignored issues."""
        issue = self.issue('Bulk-D')
        wizard = self.apply(issue, action='reopen')
        self.assertIn('Bulk-D', wizard.result_message)
        self.assertEqual(issue.state, 'new')

    def test_average_resolution_time_is_grouped(self):
        """FR-DGQ-0029: the analysis averages the resolution hours per rule."""
        first, second = self.issue('Bulk-E'), self.issue('Bulk-F')
        for issue, hours in ((first, '2026-11-11 08:00:00'), (second, '2026-11-12 08:00:00')):
            issue.write({'first_found': '2026-11-10 08:00:00'})
            with self.mock_datetime_and_now(hours):
                issue.write({'state': 'resolved'})
        self.env.flush_all()
        groups = self.Issue._read_group(
            [('state', '=', 'resolved'), ('rule_id', '=', self.rule.id)], ['rule_id'], ['resolution_hours:avg'])
        self.assertAlmostEqual(groups[0][1], 36.0, places=2)
