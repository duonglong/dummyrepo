from odoo.exceptions import UserError

from .acceptance_helpers import AcceptanceCase


class TestIssueScan(AcceptanceCase):
    """Issue counts of a scan and the status side effects of an issue.

    Requirements: FR-DGQ-0021, FR-DGQ-0022, FR-DGQ-0023, BR-DGQ-0004
    """

    def setUp(self):
        super().setUp()
        self.rule = self.make_rule('product.template', 'required', ['x_dgq_barcode'])

    def scan(self):
        return self.run_scan([self.rule], models=['product.template'])

    def issue_of(self, product):
        return self.env['data.quality.issue'].search([
            ('rule_id', '=', self.rule.id), ('res_id', '=', product.id)])

    def test_scan_counts_created_updated_and_resolved(self):
        """FR-DGQ-0021/22/23: the scan tells how many issues it raised, kept and closed."""
        desk = self.product('Desk')
        chair = self.product('Chair')
        first = self.scan()
        self.assertEqual((first.issues_created, first.issues_updated, first.issues_resolved), (2, 0, 0))
        chair.x_dgq_barcode = '1'
        second = self.scan()
        self.assertEqual((second.issues_created, second.issues_updated, second.issues_resolved), (0, 1, 1))
        self.assertEqual(self.issue_of(desk).state, 'new')
        self.assertEqual(self.issue_of(chair).state, 'resolved')

    def test_deleted_record_counts_as_resolved(self):
        """FR-DGQ-0023: the issue of a deleted record is closed and counted by the scan."""
        gone = self.product('Gone')
        self.scan()
        gone.unlink()
        scan = self.scan()
        self.assertEqual(scan.issues_resolved, 1)

    def test_ignoring_needs_a_reason(self):
        """BR-DGQ-0004: an issue is not ignored without saying why."""
        desk = self.product('Desk')
        self.scan()
        issue = self.issue_of(desk)
        with self.assertRaises(UserError):
            issue.write({'state': 'ignored'})
        self.assertEqual(issue.state, 'new')

    def test_reopening_clears_the_resolution(self):
        """FR-DGQ-0023: a reopened issue has no resolution date, hours or scan mark."""
        desk = self.product('Desk')
        self.scan()
        desk.x_dgq_barcode = '1'
        self.scan()
        issue = self.issue_of(desk)
        self.assertTrue(issue.date_resolved)
        desk.x_dgq_barcode = False
        self.scan()
        self.assertEqual(issue.state, 'new')
        self.assertFalse(issue.date_resolved)
        self.assertFalse(issue.resolved_by_scan)
