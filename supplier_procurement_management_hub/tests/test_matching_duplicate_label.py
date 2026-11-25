"""Regression tests of the possible-duplicate label on the matching page.

Requirements: FR-SPM-0075, BR-SPM-0015
"""
from odoo.tests import tagged

from .matching_common import SpmMatchingCommon


@tagged('post_install', '-at_install')
class TestDuplicateLabel(SpmMatchingCommon):
    """Requirements: FR-SPM-0075, BR-SPM-0015"""

    def setUp(self):
        super().setUp()
        self._clear_config()
        self._default_routes()

    def _twin_drafts(self):
        order = self._order(qty=80, price=14.9, receive=80)
        first = self._bill(order, 80, 14.9, ref='TWIN-REF')
        second = self._bill(order, 80, 14.9, ref='TWIN-REF')
        return first, second

    def test_b_0287_found_value_names_the_other_draft_not_the_flagged_one(self):
        """Regression test for B-0287: a bill is flagged as a possible duplicate of itself.

        Walk state: two draft bills of one supplier with the same reference are saved, the matching
        of the second one runs on save; the test cannot reach the rendered tags.
        Requirements: FR-SPM-0075
        """
        first, second = self._twin_drafts()
        exception = self._recon(second).exception_ids.filtered(lambda exc: exc.kind == 'duplicate')
        self.assertTrue(exception)
        self.assertIn(str(first.id), exception.found_value)
        self.assertNotIn(str(second.id), exception.found_value)
        self.assertNotEqual(exception.found_value, 'TWIN-REF')

    def test_b_0287_duplicate_tags_differ_from_the_flagged_bill_name(self):
        """Regression test for B-0287: the tag of the other bill reads differently from the flagged bill.

        Walk state: two draft bills with the same reference are saved; the matching page of the second
        shows the possible duplicates as tags (display name read with the tag context).
        Requirements: FR-SPM-0075
        """
        first, second = self._twin_drafts()
        recon = self._recon(second)
        tag = recon.duplicate_move_ids.with_context(spm_duplicate_label=True).display_name
        self.assertEqual(recon.duplicate_move_ids, first)
        self.assertNotEqual(tag, second.display_name)
        self.assertIn(str(first.id), tag)

    def test_b_0287_posted_duplicate_is_labelled_by_its_number(self):
        """Regression test for B-0287: a posted other bill keeps its number as label.

        Requirements: FR-SPM-0075
        """
        first, second = self._twin_drafts()
        first.action_post()
        second.invalidate_recordset()
        self.env['procurement.reconciliation'].search([('move_id', '=', second.id)]).action_spm_run_matching()
        exception = self._recon(second).exception_ids.filtered(lambda exc: exc.kind == 'duplicate')
        self.assertEqual(exception.found_value, first.name)
