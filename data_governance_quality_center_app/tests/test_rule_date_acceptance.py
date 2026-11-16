from odoo.tests import freeze_time

from .acceptance_helpers import AcceptanceCase, choice

SCAN_DAY = '2026-11-12'


@freeze_time('2026-11-12 12:00:00')
class TestRuleDateAcceptance(AcceptanceCase):
    """Acceptance tests of the date check (T-032), seen through the scan result lines.

    The scan runs on 2026-11-12 (the clock is frozen). The scan is created through the registry
    and run by the real background runner; the rule form step is left to the build.

    Requirements: FR-DGQ-0005
    """

    def _date_rule(self, include, exclude=(), field='x_dgq_born_on', model='res.partner', **vals):
        condition = choice(self.env, 'data.quality.rule', 'date_condition', include, exclude)
        return self.make_rule(model, 'date', [field], date_condition=condition, **vals)

    def test_future_date_fails_and_scan_day_passes(self):
        """SC-DGQ-0005-1: born 2027-01-01 fails 'not in the future'; born 2026-11-12 passes."""
        rule = self._date_rule(['future'], ['days', 'ahead', 'more'])
        today = self.partner('Born today', x_dgq_born_on='2026-11-12')
        ahead = self.partner('Born 2027', x_dgq_born_on='2027-01-01')
        failed = self.failing(self.run_scan([rule], models=['res.partner']), rule)
        self.assertIn(ahead.id, failed)
        self.assertNotIn(today.id, failed)

    def test_future_date_boundary_next_day_fails(self):
        """SC-DGQ-0005-1 boundary: 2026-11-13 fails, 2026-11-11 passes, an empty date is not failed."""
        rule = self._date_rule(['future'], ['days', 'ahead', 'more'])
        before = self.partner('Yesterday', x_dgq_born_on='2026-11-11')
        after = self.partner('Tomorrow', x_dgq_born_on='2026-11-13')
        empty = self.partner('No date')
        failed = self.failing(self.run_scan([rule], models=['res.partner']), rule)
        self.assertIn(after.id, failed)
        self.assertNotIn(before.id, failed)
        self.assertNotIn(empty.id, failed)

    def test_product_older_than_365_days_fails_with_date_and_limit(self):
        """SC-DGQ-0005-2: a product last updated 2025-11-01 fails 'not older than 365 days'."""
        rule = self._date_rule(['older'], field='write_date', model='product.template', date_days=365)
        old = self.product('Old')
        fresh = self.product('Fresh')
        self.env.cr.execute(
            "UPDATE product_template SET write_date = %s WHERE id = %s", ('2025-11-01 12:00:00', old.id))
        old.invalidate_recordset(['write_date'])
        failed = self.failing(self.run_scan([rule], models=['product.template']), rule)
        self.assertIn(old.id, failed)
        explanation = self.text(failed[old.id])
        self.assertIn('2025-11-01', explanation)
        self.assertIn('365', explanation)
        self.assertNotIn(fresh.id, failed)

    def test_older_than_365_days_boundary(self):
        """SC-DGQ-0005-2 boundary: exactly 365 days old (2025-11-12) passes, 2025-11-11 fails."""
        rule = self._date_rule(['older'], field='write_date', model='product.template', date_days=365)
        edge = self.product('Edge')
        over = self.product('Over')
        for product, when in ((edge, '2025-11-12 12:00:00'), (over, '2025-11-11 12:00:00')):
            self.env.cr.execute(
                "UPDATE product_template SET write_date = %s WHERE id = %s", (when, product.id))
            product.invalidate_recordset(['write_date'])
        failed = self.failing(self.run_scan([rule], models=['product.template']), rule)
        self.assertNotIn(edge.id, failed)
        self.assertIn(over.id, failed)
