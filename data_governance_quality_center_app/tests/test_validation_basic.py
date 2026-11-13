from odoo.addons.data_governance_quality_center_app.services import validation
from odoo.addons.data_governance_quality_center_app.services.validation import FAIL, NOT_APPLICABLE, PASS

from .acceptance_helpers import AcceptanceCase, choice


class TestValidationBasic(AcceptanceCase):
    """The validation service called directly, without a scan.

    Requirements: FR-DGQ-0002, FR-DGQ-0003, FR-DGQ-0004, FR-DGQ-0005
    """

    def _outcomes(self, rule, *records):
        records = records[0].browse([r.id for r in records])
        return validation.check(rule, records, today=self.day())

    def test_required_reads_empty_values(self):
        """FR-DGQ-0002: spaces, an empty list and zero are told apart."""
        rule = self.make_rule('res.partner', 'required', ['email', 'category_id'])
        tag = self.env['res.partner.category'].create({'name': 'DGQ tag'})
        spaces = self.partner('Spaces', email='  ', category_id=[(6, 0, [tag.id])])
        filled = self.partner('Filled', email='a@b.co', category_id=[(6, 0, [tag.id])])
        outcomes = self._outcomes(rule, spaces, filled)
        self.assertEqual(outcomes[(spaces.id, rule.id)].status, FAIL)
        self.assertEqual(outcomes[(spaces.id, rule.id)].params['fields'], ['email'])
        self.assertEqual(outcomes[(filled.id, rule.id)].status, PASS)

    def test_required_zero_is_a_value(self):
        """FR-DGQ-0002: a price of 0 passes."""
        rule = self.make_rule('product.template', 'required', ['list_price'])
        sample = self.product('Sample', list_price=0)
        self.assertEqual(self._outcomes(rule, sample)[(sample.id, rule.id)].status, PASS)

    def test_pattern_presets_and_empty(self):
        """FR-DGQ-0003: a preset checks the whole value; an empty value is not applicable."""
        rule = self.make_rule('res.partner', 'pattern', ['email'], pattern_preset='email')
        good = self.partner('Good', email='good@example.com')
        bad = self.partner('Bad', email='good@example.com extra')
        empty = self.partner('Empty')
        outcomes = self._outcomes(rule, good, bad, empty)
        self.assertEqual(outcomes[(good.id, rule.id)].status, PASS)
        self.assertEqual(outcomes[(bad.id, rule.id)].status, FAIL)
        self.assertEqual(outcomes[(empty.id, rule.id)].status, NOT_APPLICABLE)

    def test_range_explanation_is_rendered(self):
        """FR-DGQ-0004: the code and parameters render a sentence with the field label."""
        rule = self.make_rule('product.template', 'range', ['x_dgq_cost'], has_max=True, max_value=10)
        high = self.product('High', x_dgq_cost=12000)
        outcome = self._outcomes(rule, high)[(high.id, rule.id)]
        self.assertEqual(outcome.code, 'range_above')
        sentence = validation.explain(self.env, outcome.code, outcome.params)
        self.assertIn('DGQ Cost', sentence)
        self.assertIn('12000', sentence)

    def test_date_conditions_on_a_given_day(self):
        """FR-DGQ-0005: the day counts are measured from the day given."""
        condition = choice(self.env, 'data.quality.rule', 'date_condition', ['older'], ['ahead'])
        rule = self.make_rule(
            'res.partner', 'date', ['x_dgq_born_on'], date_condition=condition, date_days=10)
        edge = self.partner('Edge', x_dgq_born_on=self.day(-10))
        over = self.partner('Over', x_dgq_born_on=self.day(-11))
        none = self.partner('None')
        outcomes = self._outcomes(rule, edge, over, none)
        self.assertEqual(outcomes[(edge.id, rule.id)].status, PASS)
        self.assertEqual(outcomes[(over.id, rule.id)].status, FAIL)
        self.assertEqual(outcomes[(none.id, rule.id)].status, NOT_APPLICABLE)
