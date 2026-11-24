from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase


class TestCompanyLinks(TransactionCase):
    """Requirements: FR-SPM-0107, BR-SPM-0001, BR-SPM-0009"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.company_a = self.env.company
        self.company_b = self.env['res.company'].create({'name': 'Company B B0260'})
        self.department_b = self.env['hr.department'].create({'name': 'Dept B', 'company_id': self.company_b.id})
        self.product_b = self.env['product.product'].create({'name': 'Bolt B', 'company_id': self.company_b.id})
        plan = self.env['account.analytic.plan'].create({'name': 'Plan B0260'})
        self.analytic_b = self.env['account.analytic.account'].create({
            'name': 'Analytic B', 'plan_id': plan.id, 'company_id': self.company_b.id})
        self.request = self.env['procurement.request'].create({
            'needed_date': '2026-12-31', 'reason': 'Need', 'company_id': self.company_a.id,
            'line_ids': [(0, 0, {'name': 'Bolts'})]})

    def _assert_refused(self, record, vals):
        with self.assertRaises((ValidationError, UserError)):
            record.write(vals)
            self.env.flush_all()

    def test_bug_b_0260_request_refuses_a_department_of_another_company(self):
        """Regression test for B-0260: a company B department on a company A request is refused.

        Requirements: FR-SPM-0107, BR-SPM-0001
        """
        self._assert_refused(self.request, {'department_id': self.department_b.id})

    def test_bug_b_0260_request_line_refuses_a_product_of_another_company(self):
        """Regression test for B-0260: a company B product on a company A request line is refused.

        Requirements: FR-SPM-0107, BR-SPM-0001
        """
        self._assert_refused(self.request.line_ids, {'product_id': self.product_b.id})

    def test_bug_b_0260_policy_refuses_conditions_of_another_company(self):
        """Regression test for B-0260: a company A policy refuses a company B department, product or analytic account.

        Requirements: FR-SPM-0107, BR-SPM-0009
        """
        policy = self.env['procurement.approval.policy'].create({'name': 'Policy A', 'company_id': self.company_a.id})
        for vals in ({'department_ids': [(4, self.department_b.id)]},
                     {'product_ids': [(4, self.product_b.id)]},
                     {'analytic_account_ids': [(4, self.analytic_b.id)]}):
            with self.subTest(field=next(iter(vals))):
                self._assert_refused(policy, vals)
