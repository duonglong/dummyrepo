from odoo.exceptions import UserError

from .acceptance_helpers import AcceptanceCase

PATTERN = r'[A-Z]{3}-[0-9]{4}'


class TestRulePatternAcceptance(AcceptanceCase):
    """Acceptance tests of the pattern check (T-030), seen through the scan result lines.

    The scan is created through the registry and run by the real background runner; the rule
    form step is left to the build (the rule is created through the registry).

    Requirements: FR-DGQ-0003
    """

    def _scan_products(self, rule):
        return self.failing(self.run_scan([rule], models=['product.template']), rule)

    def test_pattern_passes_valid_and_fails_malformed_with_explanation(self):
        """SC-DGQ-0003-1: 'ABC-1234' passes; 'abc-12' fails, the explanation quotes value and format."""
        rule = self.make_rule('product.template', 'pattern', ['default_code'], pattern=PATTERN)
        good = self.product('Good', default_code='ABC-1234')
        bad = self.product('Bad', default_code='abc-12')
        failed = self._scan_products(rule)
        self.assertNotIn(good.id, failed)
        self.assertIn(bad.id, failed)
        explanation = self.text(failed[bad.id])
        self.assertIn('abc-12', explanation)
        self.assertTrue(PATTERN in explanation or 'format' in explanation.lower()
                        or 'pattern' in explanation.lower(), explanation)

    def test_pattern_requires_a_full_match(self):
        """SC-DGQ-0003-1 (boundary): a value that only contains a match fails."""
        rule = self.make_rule('product.template', 'pattern', ['default_code'], pattern=PATTERN)
        longer = self.product('Longer', default_code='ABC-12345')
        padded = self.product('Padded', default_code=' ABC-1234')
        failed = self._scan_products(rule)
        self.assertIn(longer.id, failed)
        self.assertIn(padded.id, failed)

    def test_pattern_does_not_fail_an_empty_field(self):
        """SC-DGQ-0003-2: a product with no internal reference is not failed by the pattern rule."""
        rule = self.make_rule('product.template', 'pattern', ['default_code'], pattern=PATTERN)
        empty = self.product('Empty')
        failed = self._scan_products(rule)
        self.assertNotIn(empty.id, failed)

    def test_pattern_with_unclosed_bracket_is_refused(self):
        """SC-DGQ-0003-3: saving a pattern with an unclosed bracket is refused, naming the pattern."""
        with self.assertRaises(UserError) as caught:
            self.make_rule('product.template', 'pattern', ['default_code'], pattern='[A-Z')
        self.assertIn('pattern', str(caught.exception).lower())
