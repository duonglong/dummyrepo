from odoo.tests import tagged

from .common import IprCommon
from ..services import reconciliation


@tagged('post_install', '-at_install')
class TestDuplicate(IprCommon):
    """Own tests of the duplicate exception.

    Requirements: BR-IPR-0002, FR-IPR-0009, FR-IPR-0010
    """

    def _bill(self, ref):
        return self.make_bill(self.supplier_acme, [{'name': 'S', 'quantity': 1, 'price_unit': 5.0}], ref=ref)

    def test_find_duplicates_ignores_case_and_edge_spaces(self):
        """BR-IPR-0002: the search matches 'INV-1' with ' inv-1 ' and skips itself."""
        first = self._bill('INV-1')
        second = self._bill(' inv-1 ')
        self.assertEqual(second._ipr_find_duplicates(), first)
        self.assertEqual(first._ipr_find_duplicates(), second)

    def test_no_reference_finds_nothing(self):
        """BR-IPR-0002: a bill without a reference is never searched."""
        self._bill(False)
        self.assertFalse(self._bill(False)._ipr_find_duplicates())

    def test_history_lists_other_bills(self):
        """FR-IPR-0009: the history data names the other bill, not amounts."""
        other = self._bill('INV-2')
        draft = self._bill('INV-2')
        draft.with_user(self.accountant).action_ipr_check()
        item = draft.ipr_history_ids.exceptions_data[0]
        self.assertEqual(item['other_bills'], other.mapped('display_name'))

    def test_verdict_counts_duplicates(self):
        """FR-IPR-0010: a duplicate alone makes the verdict waiting."""
        self.assertEqual(reconciliation.verdict([], [1]), 'waiting')
        self.assertEqual(reconciliation.verdict([], []), 'matched')
