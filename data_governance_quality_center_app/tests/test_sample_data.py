from odoo.tests import tagged

from ..services import scoring
from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestSampleData(M3Case):
    """Requirements: FR-DGQ-0080"""

    def test_sample_duplicate_rules_are_switched_off(self):
        """Requirements: FR-DGQ-0080 - the duplicate templates do not run by themselves."""
        rules = self.env['data.quality.duplicate.rule'].sudo().with_context(active_test=False).search([
            ('model_name', 'in', ('res.partner', 'product.template'))])
        self.assertTrue(rules)
        self.assertFalse(rules.filtered('active'))

    def test_sample_rules_have_a_version_and_check_their_model(self):
        """Requirements: FR-DGQ-0080 - a sample rule is a complete rule once switched on."""
        rule = self.env.ref('data_governance_quality_center_app.sample_rule_product_price')
        rule.active = True
        self.assertEqual(rule.version, 1)
        self.assertEqual(rule.model_name, 'product.template')

    def test_scan_is_illustrative_flag_defaults_off(self):
        """Requirements: FR-DGQ-0080 - a live scan is never labelled illustrative."""
        scan = self.make_scan()
        self.assertFalse(scan.is_illustrative)

    def _issue(self, rule, partner, **vals):
        return self.env['data.quality.issue'].sudo().create(dict({
            'rule_id': rule.id, 'rule_version': 1, 'res_model': 'res.partner', 'res_id': partner.id,
            'record_name': partner.name, 'severity': 'warning', 'company_id': self.cx.id}, **vals))

    def test_live_issue_counts_leave_out_illustrative_issues(self):
        """Requirements: FR-DGQ-0080 - a live figure counts live results only, never a demo issue."""
        rule = self.env.ref('data_governance_quality_center_app.sample_rule_contact_email')
        partners = self.env['res.partner'].create([{'name': 'Live P'}, {'name': 'Demo P'}])
        self._issue(rule, partners[0])
        self._issue(rule, partners[1], is_illustrative=True)
        counts = scoring.open_issue_counts(
            self.env, self.env['ir.model'].sudo().search([('model', '=', 'res.partner')]), self.cx.id)
        self.assertEqual(counts[('res.partner', self.cx.id)]['warning'], 1)

    def test_recent_issues_say_which_are_illustrative(self):
        """Requirements: FR-DGQ-0080 - each latest issue row of the dashboard carries its own illustrative flag."""
        rule = self.env.ref('data_governance_quality_center_app.sample_rule_contact_email')
        partner = self.env['res.partner'].create({'name': 'Demo Q'})
        issue = self._issue(rule, partner, is_illustrative=True)
        rows = self.env['data.quality.dashboard'].sudo()._recent_issues(self.cx, None)
        self.assertTrue(next(row for row in rows if row['id'] == issue.id)['is_illustrative'])
