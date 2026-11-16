from .acceptance_helpers import AcceptanceCase


class TestCompanyIsolationAcceptance(AcceptanceCase):
    """Acceptance tests of the company isolation of quality data (T-042).

    The issue list is read as the user's search; the list screen itself is client side.

    Requirements: FR-DGQ-0071
    """

    def _issue(self, rule, res_model, res_id, company, name):
        return self.env['data.quality.issue'].create({
            'rule_id': rule.id, 'res_model': res_model, 'res_id': res_id,
            'company_id': company.id if company else False,
            'severity': 'warning', 'record_name': name})

    def test_operator_of_company_a_sees_only_company_a_issues(self):
        """SC-DGQ-0071-1: issues of A and B; an operator allowed A only sees those of A."""
        rule = self.make_rule('product.template', 'required', ['x_dgq_barcode'], company=False)
        prod_a = self.product('Desk A', company=self.cx)
        prod_b = self.product('Desk B', company=self.cy)
        issue_a = self._issue(rule, 'product.template', prod_a.id, self.cx, 'Desk A')
        issue_b = self._issue(rule, 'product.template', prod_b.id, self.cy, 'Desk B')
        operator = self.user('operator', login='dgq_iso_op', company=self.cx)
        self.env.flush_all()
        seen = self.env['data.quality.issue'].with_user(operator).search([])
        self.assertIn(issue_a, seen)
        self.assertNotIn(issue_b, seen)
        self.assertEqual(set(seen.mapped('company_id')), {self.cx})

    def test_issue_without_company_is_visible_to_all(self):
        """SC-DGQ-0071-1 (rule text): a record with no company is visible to every company."""
        rule = self.make_rule('product.template', 'required', ['x_dgq_barcode'], company=False)
        prod = self.product('Shared desk', company=self.cx)
        shared = self._issue(rule, 'product.template', prod.id, False, 'Shared desk')
        operator = self.user('operator', login='dgq_iso_op3', company=self.cx)
        self.env.flush_all()
        self.assertIn(shared, self.env['data.quality.issue'].with_user(operator).search([]))

    def test_issue_on_record_the_operator_cannot_read_is_hidden(self):
        """SC-DGQ-0071-2: an issue on a record of a model the operator cannot read is not shown.

        The employee model belongs to an app this module does not depend on, so the scenario's
        employee is stood in for by a scheduled action, a model an operator cannot read either.
        """
        cron = self.env['ir.cron'].sudo().search([], limit=1)
        self.assertTrue(cron)
        prod_rule = self.make_rule('product.template', 'required', ['x_dgq_barcode'])
        hidden = self._issue(prod_rule, 'ir.cron', cron.id, self.cx, 'Hidden record')
        prod = self.product('Visible desk', company=self.cx)
        visible = self._issue(prod_rule, 'product.template', prod.id, self.cx, 'Visible desk')
        operator = self.user('operator', login='dgq_iso_op2', company=self.cx)
        self.env.flush_all()
        seen = self.env['data.quality.issue'].with_user(operator).search([])
        self.assertIn(visible, seen)
        self.assertNotIn(hidden, seen)
