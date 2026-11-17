from odoo.exceptions import UserError

from .acceptance_helpers import AcceptanceCase


class TestRuleRangeAcceptance(AcceptanceCase):
    """Acceptance tests of the number range check (T-031), seen through the scan result lines.

    The scan is created through the registry and run by the real background runner; the rule
    form step is left to the build (the rule is created through the registry).

    Requirements: FR-DGQ-0004
    """

    def _range_rule(self, **vals):
        return self.make_rule('product.template', 'range', ['x_dgq_cost'], **vals)

    def _scan_products(self, rule):
        return self.failing(self.run_scan([rule], models=['product.template']), rule)

    def test_range_bounds_count_as_inside(self):
        """SC-DGQ-0004-1: costs -5 and 12000 fail; 0 and 10000 pass; just outside a bound fails."""
        rule = self._range_rule(has_min=True, min_value=0, has_max=True, max_value=10000)
        costs = {-5: True, -0.01: True, 0: False, 10000: False, 10000.01: True, 12000: True}
        products = {cost: self.product('Cost %s' % cost, x_dgq_cost=cost) for cost in costs}
        failed = self._scan_products(rule)
        for cost, fails in costs.items():
            self.assertEqual(products[cost].id in failed, fails, cost)

    def test_range_only_a_minimum_checks_a_floor(self):
        """FR-DGQ-0004: a rule with only a minimum has no ceiling."""
        rule = self._range_rule(has_min=True, min_value=0, has_max=False)
        low = self.product('Low', x_dgq_cost=-1)
        high = self.product('High', x_dgq_cost=10 ** 9)
        failed = self._scan_products(rule)
        self.assertIn(low.id, failed)
        self.assertNotIn(high.id, failed)

    def test_range_only_a_maximum_checks_a_ceiling(self):
        """FR-DGQ-0004: a rule with only a maximum has no floor."""
        rule = self._range_rule(has_min=False, has_max=True, max_value=100)
        low = self.product('Low', x_dgq_cost=-10 ** 6)
        high = self.product('High', x_dgq_cost=101)
        failed = self._scan_products(rule)
        self.assertNotIn(low.id, failed)
        self.assertIn(high.id, failed)

    def test_range_minimum_above_maximum_is_refused(self):
        """SC-DGQ-0004-2: minimum 100 and maximum 10 are refused with a message naming both."""
        with self.assertRaises(UserError) as caught:
            self._range_rule(has_min=True, min_value=100, has_max=True, max_value=10)
        message = str(caught.exception).lower()
        self.assertIn('minimum', message)
        self.assertIn('maximum', message)

    def test_range_without_a_bound_is_refused(self):
        """FR-DGQ-0004: a range rule with neither bound cannot work and is refused."""
        with self.assertRaises(UserError):
            self._range_rule(has_min=False, has_max=False)
