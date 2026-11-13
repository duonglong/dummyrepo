from odoo.exceptions import UserError

from .acceptance_helpers import AcceptanceCase

STATES = ('new', 'assigned', 'in_progress', 'resolved', 'ignored')
ALLOWED_MOVES = {
    'new': {'assigned', 'in_progress', 'resolved', 'ignored'},
    'assigned': {'in_progress', 'resolved', 'ignored', 'new'},
    'in_progress': {'resolved', 'ignored', 'assigned'},
    'resolved': {'new'},
    'ignored': {'new'},
}


class TestIssueScanAcceptance(AcceptanceCase):
    """Acceptance tests of T-011: one issue per record and rule, created, updated, closed, reopened.

    Issues come from real scans; the status moves of BR-DGQ-0004 are tried on the model with
    ``write`` (the message naming the allowed statuses and the history belong to T-012).

    Requirements: BR-DGQ-0003, BR-DGQ-0004, FR-DGQ-0021, FR-DGQ-0022, FR-DGQ-0023
    """

    def setUp(self):
        super().setUp()
        self.ann = self.user('operator', login='dgq_m2_ann', company=self.cx)
        self.rule = self.make_rule(
            'product.template', 'required', ['x_dgq_barcode'], name='Product needs barcode')
        self.rule.severity = 'critical'  # a change of the check: version 2

    def scan(self, rules=None):
        return self.run_scan(rules or [self.rule], models=['product.template'])

    def issues(self, record, rule=None):
        return self.env['data.quality.issue'].search([
            ('rule_id', '=', (rule or self.rule).id),
            ('res_model', '=', record._name), ('res_id', '=', record.id)])

    def messages(self, issue):
        self.env.flush_all()
        return ' '.join(issue.message_ids.mapped('body')).lower()

    # ---- FR-DGQ-0021 -------------------------------------------------------

    def test_failed_check_creates_one_new_issue(self):
        """SC-DGQ-0021-1: 'Desk' without barcode gets one New issue with everything the scan knows."""
        self.assertEqual(self.rule.version, 2)
        desk = self.product('Desk')
        scan = self.scan()
        issue = self.issues(desk)
        self.assertEqual(len(issue), 1)
        self.assertEqual(issue.state, 'new')
        self.assertEqual(issue.company_id, self.cx)
        self.assertEqual(issue.rule_id, self.rule)
        self.assertEqual(issue.rule_version, 2)
        self.assertEqual(issue.severity, 'critical')
        self.assertTrue(issue.explanation)
        self.assertEqual(issue.scan_id, scan.sudo())
        self.assertEqual(issue.first_scan_id, scan.sudo())
        self.assertTrue(issue.first_found)
        self.assertTrue(issue.last_found)
        self.assertEqual(issue.record_name, 'Desk')
        self.assertEqual(scan.issues_created, 1)

    def test_priority_starts_from_the_severity(self):
        """FR-DGQ-0021: information starts low, warning normal, critical high."""
        rules = {
            'info': self.make_rule('product.template', 'required', ['x_dgq_start'], severity='info'),
            'warning': self.make_rule('product.template', 'required', ['x_dgq_end'], severity='warning'),
            'critical': self.rule,
        }
        desk = self.product('Desk')
        self.scan(list(rules.values()))
        expected = {'info': 'low', 'warning': 'normal', 'critical': 'high'}
        for severity, rule in rules.items():
            issue = self.issues(desk, rule)
            self.assertEqual(len(issue), 1, severity)
            self.assertEqual(issue.priority, expected[severity])

    def test_passing_record_gets_no_issue(self):
        """FR-DGQ-0021: only a failed check raises an issue."""
        chair = self.product('Chair', x_dgq_barcode='123')
        self.scan()
        self.assertFalse(self.issues(chair))

    def test_second_issue_for_the_same_record_and_rule_is_refused(self):
        """BR-DGQ-0003: the pair of rule and record holds one issue; a second one cannot be stored."""
        desk = self.product('Desk')
        vals = {
            'rule_id': self.rule.id, 'res_model': 'product.template', 'res_id': desk.id,
            'company_id': self.cx.id, 'severity': 'critical',
        }
        Issue = self.env['data.quality.issue']
        Issue.create(vals)
        with self.assertRaises(Exception):  # IntegrityError or a validation error
            with self.cr.savepoint():
                Issue.create(vals)

    # ---- FR-DGQ-0022 -------------------------------------------------------

    def test_repeated_scans_update_the_same_issue(self):
        """SC-DGQ-0022-1: two more scans leave one Assigned issue, with its last scan and date updated."""
        desk = self.product('Desk')
        first = self.scan()
        issue = self.issues(desk)
        issue.write({'state': 'assigned', 'user_id': self.ann.id})
        found = issue.last_found
        self.scan()
        third = self.scan()
        issue = self.issues(desk)
        self.assertEqual(len(issue), 1)
        self.assertEqual(issue.state, 'assigned')
        self.assertEqual(issue.scan_id, third.sudo())
        self.assertEqual(issue.first_scan_id, first.sudo())
        self.assertGreaterEqual(issue.last_found, found)

    def test_repeated_scan_takes_the_new_rule_version(self):
        """FR-DGQ-0022: the update carries the rule version the latest scan used."""
        desk = self.product('Desk')
        self.scan()
        self.rule.severity = 'warning'
        self.assertEqual(self.rule.version, 3)
        self.scan()
        issue = self.issues(desk)
        self.assertEqual(len(issue), 1)
        self.assertEqual(issue.rule_version, 3)

    def test_ignored_issue_stays_ignored(self):
        """SC-DGQ-0022-2: a finding that is still true leaves an Ignored issue alone and raises no new one."""
        desk = self.product('Desk')
        self.scan()
        issue = self.issues(desk)
        issue.write({'state': 'ignored', 'ignore_reason': 'Accepted by the business'})
        self.scan()
        issue = self.issues(desk)
        self.assertEqual(len(issue), 1)
        self.assertEqual(issue.state, 'ignored')

    # ---- FR-DGQ-0023 -------------------------------------------------------

    def test_scan_resolves_the_issue_of_a_fixed_record(self):
        """SC-DGQ-0023-1: an In Progress issue is Resolved by the scan once 'Desk' has a barcode."""
        desk = self.product('Desk')
        self.scan()
        issue = self.issues(desk)
        issue.write({'state': 'in_progress'})
        desk.x_dgq_barcode = '4006381333931'
        self.scan()
        issue = self.issues(desk)
        self.assertEqual(len(issue), 1)
        self.assertEqual(issue.state, 'resolved')
        self.assertTrue(issue.resolved_by_scan)
        self.assertTrue(issue.date_resolved)
        self.assertGreaterEqual(issue.resolution_hours, 0.0)
        self.assertIn('resolved by scan', self.messages(issue))

    def test_scan_resolves_new_and_assigned_issues_too(self):
        """FR-DGQ-0023: New and Assigned issues are closed by the scan as well."""
        new = self.product('New one')
        assigned = self.product('Assigned one')
        self.scan()
        self.issues(assigned).write({'state': 'assigned', 'user_id': self.ann.id})
        (new | assigned).write({'x_dgq_barcode': '1'})
        self.scan()
        self.assertEqual(self.issues(new).state, 'resolved')
        self.assertEqual(self.issues(assigned).state, 'resolved')

    def test_scan_resolves_the_issue_of_a_deleted_record(self):
        """FR-DGQ-0023: a record deleted since the last scan has its issues resolved by the scan."""
        gone = self.product('Gone')
        gone_id = gone.id
        self.scan()
        issue = self.issues(gone)
        self.assertEqual(issue.state, 'new')
        gone.unlink()
        self.scan()
        self.assertEqual(issue.state, 'resolved')
        self.assertTrue(issue.resolved_by_scan)
        self.assertEqual(issue.res_id, gone_id)

    def test_scan_resolves_when_the_rule_no_longer_applies(self):
        """FR-DGQ-0023: a record that leaves the 'applies to' filter has its issue resolved."""
        rule = self.make_rule(
            'res.partner', 'condition', applies_domain="[('is_company', '=', True)]",
            condition_domain="[('vat', '!=', False)]")
        acme = self.partner('Acme', is_company=True)
        self.run_scan([rule], models=['res.partner'])
        issue = self.issues(acme, rule)
        self.assertEqual(issue.state, 'new')
        acme.is_company = False
        self.run_scan([rule], models=['res.partner'])
        self.assertEqual(issue.state, 'resolved')

    def test_scan_reopens_the_same_resolved_issue(self):
        """SC-DGQ-0023-2: when the barcode is cleared again, the same issue is New with a note."""
        desk = self.product('Desk')
        self.scan()
        issue = self.issues(desk)
        desk.x_dgq_barcode = '1'
        self.scan()
        self.assertEqual(issue.state, 'resolved')
        desk.x_dgq_barcode = False
        self.scan()
        again = self.issues(desk)
        self.assertEqual(again, issue)
        self.assertEqual(len(again), 1)
        self.assertEqual(again.state, 'new')
        self.assertIn('reopen', self.messages(again))
        self.assertEqual(self.env['data.quality.issue'].search_count([('rule_id', '=', self.rule.id)]), 1)

    # ---- BR-DGQ-0004 -------------------------------------------------------

    def _issue_in(self, state):
        product = self.product('Move %s' % self.env['data.quality.issue'].search_count([]))
        issue = self.env['data.quality.issue'].create({
            'rule_id': self.rule.id, 'res_model': 'product.template', 'res_id': product.id,
            'company_id': self.cx.id, 'severity': 'warning'})
        self.assertEqual(issue.state, 'new')
        if state != 'new':
            self._move(issue, state)
        self.assertEqual(issue.state, state)
        return issue

    def _move(self, issue, target):
        vals = {'state': target}
        if target == 'assigned':
            vals['user_id'] = self.ann.id
        if target == 'ignored':
            vals['ignore_reason'] = 'Accepted by the business'
        issue.write(vals)

    def test_only_the_allowed_status_changes_are_accepted(self):
        """BR-DGQ-0004: each move of the table is accepted and every other move is refused."""
        for source in STATES:
            for target in STATES:
                if source == target:
                    continue
                issue = self._issue_in(source)
                if target in ALLOWED_MOVES[source]:
                    self._move(issue, target)
                    self.assertEqual(issue.state, target, (source, target))
                else:
                    with self.assertRaises(UserError, msg=(source, target)):
                        with self.cr.savepoint():
                            self._move(issue, target)

    def test_assigning_and_unassigning_moves_the_status(self):
        """BR-DGQ-0004: a person on a New issue makes it Assigned; removing the person makes it New."""
        issue = self._issue_in('new')
        issue.user_id = self.ann
        self.assertEqual(issue.state, 'assigned')
        issue.user_id = False
        self.assertEqual(issue.state, 'new')
