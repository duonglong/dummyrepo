from odoo.tests import tagged

from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestDashboardDrillAcceptance(M3Case):
    """Acceptance tests of the drill-down from a dashboard figure.

    Requirements: FR-DGQ-0036
    The click belongs to the client widget; the server entry
    ``data.quality.dashboard.drill_down`` returns the window action the click opens, called as a viewer.
    """

    def issue(self, rule, partner, company, **vals):
        return self.env['data.quality.issue'].create(dict({
            'rule_id': rule.id, 'res_model': 'res.partner', 'res_id': partner.id,
            'severity': rule.severity, 'company_id': company.id}, **vals))

    def listed(self, action):
        self.assertEqual(action['type'], 'ir.actions.act_window')
        return self.env['data.quality.issue'].with_user(self.viewer).search(action['domain'])

    def test_critical_figure_opens_the_critical_open_issues_of_the_company(self):
        """SC-DGQ-0036-1: the 3 critical issues of company A open as a list of those 3 issues only."""
        critical = self.make_rule('res.partner', 'required', ['name'], severity='critical')
        warning = self.make_rule('res.partner', 'required', ['email'], severity='warning')
        other = self.make_rule('res.partner', 'required', ['phone'], severity='critical', company=self.cy)
        partners = self.env['res.partner'].create([{'name': 'P%s' % i} for i in range(4)])
        mine = self.env['data.quality.issue']
        for partner in partners[:3]:
            mine |= self.issue(critical, partner, self.cx)
        self.issue(critical, partners[3], self.cx, state='resolved')  # closed: not behind the figure
        self.issue(warning, partners[0], self.cx)  # not critical
        self.issue(other, partners[0], self.cy)  # other company
        action = self.env['data.quality.dashboard'].with_user(self.viewer).drill_down(
            'critical_issues', company_ids=[self.cx.id])
        self.assertEqual(action['res_model'], 'data.quality.issue')
        self.assertEqual(self.listed(action), mine)

    def test_model_score_opens_the_open_issues_of_that_model(self):
        """SC-DGQ-0036-2: clicking Contact opens the open issues of Contact, not those of another model."""
        rule = self.make_rule('res.partner', 'required', ['name'], severity='warning')
        product_rule = self.make_rule('product.template', 'required', ['barcode'], severity='warning')
        partner = self.env['res.partner'].create({'name': 'Bad'})
        product = self.env['product.template'].create({'name': 'Prod'})
        mine = self.issue(rule, partner, self.cx)
        self.issue(rule, self.env['res.partner'].create({'name': 'Fixed'}), self.cx, state='resolved')
        self.env['data.quality.issue'].create({
            'rule_id': product_rule.id, 'res_model': 'product.template', 'res_id': product.id,
            'severity': 'warning', 'company_id': self.cx.id})
        action = self.env['data.quality.dashboard'].with_user(self.viewer).drill_down(
            'model', company_ids=[self.cx.id], model_id=self.model('res.partner').id)
        self.assertEqual(action['res_model'], 'data.quality.issue')
        self.assertEqual(self.listed(action), mine)
