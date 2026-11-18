"""Acceptance tests of T-025 (policies, reconciliation, discrepancies, tolerances).

Requirements: BR-SPM-0014, FR-SPM-0071, FR-SPM-0072, FR-SPM-0073, FR-SPM-0074

A bill is matched when it is saved (design ADR-SPM-006), so the tests create
the draft bill and read its reconciliation. The exception halves of the
scenarios (SC-SPM-0073-*, SC-SPM-0074-*) are tested under T-026, which builds
the exception model.
"""
from odoo.tests import tagged

from .matching_common import SpmMatchingCommon


@tagged('post_install', '-at_install')
class TestMatchingAcceptance(SpmMatchingCommon):
    """Requirements: FR-SPM-0071, FR-SPM-0072, FR-SPM-0073, FR-SPM-0074, BR-SPM-0014"""

    def setUp(self):
        super().setUp()
        self._clear_config()
        self._policy('three_way')
        self._tolerance('price', percent=2.0, absolute=0.0)
        self._tolerance('quantity', percent=0.0, absolute=0.0)

    def _line(self, bill):
        recon = self._recon(bill)
        self.assertEqual(len(recon), 1, "saving a purchase-linked draft bill creates its reconciliation")
        return recon, recon.line_ids

    def test_sc_0071_1_vendor_policy_beats_company_default(self):
        """Requirements: FR-SPM-0071. Scenario SC-SPM-0071-1."""
        self._policy('two_way', self.cleanco)
        acme_bill = self._bill(self._order(self.acme, receive=100), 100, 11.5)
        clean_bill = self._bill(self._order(self.cleanco, receive=100), 100, 11.5)
        self.assertEqual(self._recon(acme_bill).method, 'three_way')
        self.assertEqual(self._recon(clean_bill).method, 'two_way')

    def test_sc_0072_1_reconciliation_shows_ordered_received_billed(self):
        """Requirements: FR-SPM-0072. Scenario SC-SPM-0072-1."""
        bill = self._bill(self._order(receive=90), 100, 11.5)
        _recon, line = self._line(bill)
        self.assertEqual(line.ordered_qty, 100)
        self.assertEqual(line.received_qty, 90)
        self.assertEqual(line.billed_qty, 100)
        self.assertAlmostEqual(line.ordered_price, 11.5)
        self.assertAlmostEqual(line.billed_price, 11.5)
        self.assertEqual(line.move_line_id.move_id, bill)

    def test_sc_0072_1_saving_the_bill_again_refreshes_the_figures(self):
        """Requirements: FR-SPM-0072. Refresh on change (second part of SC-SPM-0072-1)."""
        bill = self._bill(self._order(receive=90), 100, 11.5)
        bill.invoice_line_ids.write({'quantity': 80})
        _recon, line = self._line(bill)
        self.assertEqual(line.billed_qty, 80)
        self.assertEqual(len(self._recon(bill)), 1, "one reconciliation per bill")

    def test_sc_0073_1_quantity_and_price_differences_are_reported(self):
        """Requirements: FR-SPM-0073. Scenario SC-SPM-0073-1 (the line figures)."""
        bill = self._bill(self._order(receive=90), 100, 11.8)
        _recon, line = self._line(bill)
        self.assertAlmostEqual(line.qty_difference, 10)
        self.assertAlmostEqual(line.price_difference, 0.30)

    def test_sc_0073_1_two_way_compares_with_the_ordered_quantity(self):
        """Requirements: FR-SPM-0073. Two-way variant of SC-SPM-0073-1."""
        self._policy('two_way', self.acme)
        bill = self._bill(self._order(receive=90), 100, 11.5)
        _recon, line = self._line(bill)
        self.assertLessEqual(line.qty_difference, 0)
        bill2 = self._bill(self._order(receive=90), 101, 11.5)
        _recon2, line2 = self._line(bill2)
        self.assertAlmostEqual(line2.qty_difference, 1)

    def test_sc_0073_2_currency_difference_is_reported(self):
        """Requirements: FR-SPM-0073. Scenario SC-SPM-0073-2 (the line currencies)."""
        order = self._order(currency=self.eur, receive=100)
        bill = self._bill(order, 100, 11.5, currency=self.usd)
        _recon, line = self._line(bill)
        self.assertEqual(line.order_currency_id, self.eur)
        self.assertEqual(line.currency_id, self.usd)

    def test_sc_0073_3_billing_less_than_received_is_no_difference(self):
        """Requirements: FR-SPM-0073. Scenario SC-SPM-0073-3."""
        bill = self._bill(self._order(receive=90), 60, 11.5)
        _recon, line = self._line(bill)
        self.assertLessEqual(line.qty_difference, 0)
        self.assertAlmostEqual(line.price_difference, 0.0)

    def test_sc_0073_3_billing_exactly_the_received_quantity_is_no_difference(self):
        """Requirements: FR-SPM-0073. Boundary: billed equals received."""
        bill = self._bill(self._order(receive=90), 90, 11.5)
        _recon, line = self._line(bill)
        self.assertLessEqual(line.qty_difference, 0)

    def test_sc_0073_3_billing_one_above_received_is_a_difference(self):
        """Requirements: FR-SPM-0073. Boundary: one unit above received."""
        bill = self._bill(self._order(receive=90), 91, 11.5)
        _recon, line = self._line(bill)
        self.assertAlmostEqual(line.qty_difference, 1)

    def test_sc_0074_1_difference_within_tolerance_is_shown_as_such(self):
        """Requirements: FR-SPM-0074. Scenario SC-SPM-0074-1: 2 percent or 5 EUR."""
        self._clear_config()
        self._policy('two_way')
        self._tolerance('price', percent=2.0, absolute=5.0)
        bill = self._bill(self._order(qty=10, receive=10), 10, 11.7)
        _recon, line = self._line(bill)
        self.assertAlmostEqual(line.price_difference, 0.20)
        self.assertAlmostEqual(abs(line.amount_difference), 2.0)
        self.assertTrue(line.price_within_tolerance)

    def test_sc_0074_2_difference_above_tolerance_is_shown_as_such(self):
        """Requirements: FR-SPM-0074. Scenario SC-SPM-0074-2: 8.7 percent, 100 EUR."""
        self._clear_config()
        self._policy('two_way')
        self._tolerance('price', percent=2.0, absolute=5.0)
        bill = self._bill(self._order(qty=100, receive=100), 100, 12.5)
        _recon, line = self._line(bill)
        self.assertAlmostEqual(abs(line.amount_difference), 100.0)
        self.assertFalse(line.price_within_tolerance)

    def test_br_0014_the_absolute_amount_alone_accepts_a_difference(self):
        """Requirements: BR-SPM-0014. Within the amount although above the percentage."""
        self._clear_config()
        self._policy('two_way')
        self._tolerance('price', percent=0.5, absolute=5.0)
        bill = self._bill(self._order(qty=10, receive=10), 10, 11.7)
        _recon, line = self._line(bill)
        self.assertTrue(line.price_within_tolerance)

    def test_br_0014_vendor_tolerance_beats_company_default(self):
        """Requirements: BR-SPM-0014. Company 2 percent, Acme 0.5 percent."""
        self._clear_config()
        self._policy('two_way')
        self._tolerance('price', percent=2.0)
        self._tolerance('price', percent=0.5, partner=self.acme)
        # 11.60 against 11.50 is 0.87 percent
        acme_bill = self._bill(self._order(self.acme, receive=100), 100, 11.6)
        other_bill = self._bill(self._order(self.cleanco, receive=100), 100, 11.6)
        self.assertFalse(self._recon(acme_bill).line_ids.price_within_tolerance)
        self.assertTrue(self._recon(other_bill).line_ids.price_within_tolerance)

    def test_br_0014_a_tolerance_whose_band_misses_the_bill_total_is_skipped(self):
        """Requirements: BR-SPM-0014. The vendor tolerance band 5000-10000 excludes a 1,160 bill."""
        self._clear_config()
        self._policy('two_way')
        self._tolerance('price', percent=2.0)
        self._tolerance('price', percent=0.1, partner=self.acme,
                        amount_from=5000.0, amount_to=10000.0)
        bill = self._bill(self._order(self.acme, receive=100), 100, 11.6)
        self.assertTrue(self._recon(bill).line_ids.price_within_tolerance)
