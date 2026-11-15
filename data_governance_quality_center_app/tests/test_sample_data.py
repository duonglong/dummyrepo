from odoo.tests import tagged

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
