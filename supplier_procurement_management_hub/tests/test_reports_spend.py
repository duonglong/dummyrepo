"""Own tests of the report views of T-031.

Requirements: FR-SPM-0099, FR-SPM-0100, FR-SPM-0101
"""
from .comparison_common import SpmComparisonCase


class TestReportsSpend(SpmComparisonCase):
    """Requirements: FR-SPM-0099, FR-SPM-0100"""

    def test_fr_0100_price_trend_is_empty_without_purchases(self):
        """FR-SPM-0100: a product nobody bought in the period has no price rows."""
        rows = self.env['procurement.report.price'].with_user(self.manager).spm_price_trend(
            self.desk, '2026-07-01', '2026-09-30')
        self.assertEqual(rows, [])

    def test_fr_0099_spend_view_is_read_only_for_a_buyer(self):
        """FR-SPM-0099: a buyer reads the spend rows and holds no write right on them."""
        spend = self.env['procurement.report.spend'].with_user(self.buyer)
        self.assertTrue(spend.has_access('read'))
        self.assertFalse(spend.has_access('write'))
