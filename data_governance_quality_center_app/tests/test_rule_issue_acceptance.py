from .acceptance_helpers import AcceptanceCase


class TestRuleIssueAcceptance(AcceptanceCase):
    """Issue part of SC-DGQ-0010-1, split out of test_rule_acceptance.py.

    It needs the scan results (T-010) and the issues (T-011).
    Requirements: FR-DGQ-0010
    """

    def test_switch_off_keeps_issues_and_leaves_the_rule_out(self):
        """SC-DGQ-0010-1: a switched-off rule is not checked, its 4 issues and history stay."""
        rule = self.make_rule('res.partner', 'required', ['vat'], name='Vendor needs VAT')
        partners = [self.partner('NoVat %s' % n) for n in range(4)]
        first = self.run_scan(models=['res.partner'])
        self.assertEqual(len(self.failing(first, rule)), 4)
        Issue = self.env['data.quality.issue']
        issues = Issue.search([('rule_id', '=', rule.id)])
        self.assertEqual(len(issues), 4)
        states = {issue.id: issue.state for issue in issues}
        history = {issue.id: len(issue.message_ids) for issue in issues}
        rule.active = False
        second = self.run_scan(models=['res.partner'])
        self.assertFalse(self.failing(second, rule))
        issues = Issue.search([('rule_id', '=', rule.id)])
        self.assertEqual(len(issues), 4)
        self.assertEqual({issue.id: issue.state for issue in issues}, states)
        self.assertEqual({issue.id: len(issue.message_ids) for issue in issues}, history)
        self.assertEqual(len(partners), 4)
