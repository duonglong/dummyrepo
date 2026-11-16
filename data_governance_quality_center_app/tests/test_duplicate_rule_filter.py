from odoo import Command
from odoo.exceptions import ValidationError

from .acceptance_m2b_helpers import M2BCase

# operator x path shape; every row names a leaf a scan may not read once 'comment' and 'vat' are sensitive
REFUSED_FILTERS = {
    'plain field, comparison': "[('comment', 'ilike', 'x')]",
    'plain field, in': "[('comment', 'in', ['x'])]",
    'plain field, not stored': "[('display_name', 'ilike', 'x')]",
    'link to field, comparison': "[('parent_id.comment', 'ilike', 'x')]",
    'link to field, like': "[('bank_ids.acc_number', '=like', 'DE%')]",
    'link to field, in': "[('parent_id.comment', 'in', ['x'])]",
    'link compared to text, ilike': "[('parent_id', 'ilike', 'FR12')]",
    'link compared to text, equals': "[('parent_id', '=', 'FR12')]",
    'child_of with text': "[('parent_id', 'child_of', 'FR12')]",
    'parent_of with text': "[('parent_id', 'parent_of', 'FR12')]",
    'any': "[('child_ids', 'any', [('comment', 'ilike', 'x')])]",
    'not any': "[('child_ids', 'not any', [('comment', 'ilike', 'x')])]",
    'path into an unmarked model': "[('company_id.name', '=', 'X')]",
}


class TestDuplicateRuleFilter(M2BCase):
    """Regression tests for B-0212: the filter of a duplicate rule reaches no field a scan may not read.

    A manager saves the rule through the model and starts a check as the buttons do; the
    check reads the records of the filter in sudo. The form widgets are client side.

    Requirements: BR-DGQ-0010, FR-DGQ-0038, FR-DGQ-0075
    """

    def setUp(self):
        super().setUp()
        for name in ('comment', 'vat'):
            self.env['data.quality.field.config'].create({
                'field_id': self.fld('res.partner', name).id, 'status': 'sensitive'})

    def _rule(self, domain):
        return self.env['data.quality.duplicate.rule'].with_user(self.manager_a).create({
            'name': 'Contacts with a filter',
            'model_id': self.model('res.partner').id,
            'domain': domain,
            'field_line_ids': [Command.create({
                'field_id': self.fld('res.partner', 'email').id, 'method': 'exact', 'weight': 1})],
        })

    def test_b_0212_a_filter_on_a_field_a_scan_may_not_read_is_refused(self):
        """B-0212: each operator and path shape is refused when the rule is saved."""
        for shape, domain in REFUSED_FILTERS.items():
            with self.subTest(shape=shape), self.assertRaises(ValidationError):
                self._rule(domain)

    def test_b_0212_changing_the_filter_of_a_saved_rule_is_refused(self):
        """B-0212: the same refusal holds when a saved rule's filter is edited."""
        rule = self._rule("[('name', 'ilike', 'a')]")
        for shape, domain in REFUSED_FILTERS.items():
            with self.subTest(shape=shape), self.assertRaises(ValidationError):
                rule.write({'domain': domain})

    def test_b_0212_a_filter_on_an_allowed_field_is_saved(self):
        """FR-DGQ-0038: a filter on stored, allowed fields still saves."""
        self.assertTrue(self._rule("[('name', 'ilike', 'a'), ('parent_id', 'child_of', [1])]").exists())

    def test_b_0212_a_mark_set_after_saving_stops_the_check(self):
        """B-0212: the marks change after the filter was saved; starting a check reads them again."""
        rule = self._rule("[('function', 'ilike', 'a')]")
        self.env['data.quality.field.config'].create({
            'field_id': self.fld('res.partner', 'function').id, 'status': 'sensitive'})
        check = self.env['data.quality.duplicate.check'].with_user(self.manager_a).create({'rule_id': rule.id})
        with self.assertRaises(ValidationError):
            check.action_start()
