"""Regression tests of the side-by-side grid of the quotation comparison.

Requirements: FR-SPM-0035, FR-SPM-0038
"""
from lxml import html

from odoo.tests import tagged

from .comparison_common import SpmComparisonCase


@tagged('post_install', '-at_install')
class TestComparisonGrid(SpmComparisonCase):
    """Requirements: FR-SPM-0035, FR-SPM-0038"""

    def _grid(self):
        """Open the comparison as the buyer does: evaluate the event, then press Compare."""
        event = self._sourcing(
            self.acme | self.beta, lines=[('Chair', self.chair, 60.0), ('Desk', self.desk, 10.0)],
            criteria=[('Lead time', 'lead_time', 'lower', 100.0)])
        acme, beta = self._response(event, self.acme), self._response(event, self.beta)
        chair, desk = event.line_ids
        self._quote(acme, [(chair, 10.0, 5), (desk, 100.0, 5)])
        self._quote(beta, [(chair, 12.0, 10)])
        event.with_user(self.buyer).action_spm_evaluate()
        comparison = self._compare(event)
        table = html.fromstring(comparison.with_user(self.buyer).grid_html)
        return acme, beta, table

    @staticmethod
    def _rows(table):
        return {row.xpath('string(th)').strip(): row for row in table.xpath('//tr')}

    def test_b_0288_suppliers_are_columns_ordered_by_rank(self):
        """Regression test for B-0288 and B-0289: the comparison shows the suppliers side by side as columns,
        best rank first, with the weighted score and rank of each.

        Walk state: an event of two lines and a lead-time criterion is sent, both suppliers quote (one leaves
        the desk out), the buyer evaluates, then presses Compare; the test cannot reach the rendered browser look.
        Requirements: FR-SPM-0035, FR-SPM-0038
        """
        acme, beta, table = self._grid()
        headers = [header.text_content().strip() for header in table.xpath('//thead//th[@scope="col"]')]
        self.assertEqual(headers, [self.acme.name, self.beta.name])
        rows = self._rows(table)
        self.assertEqual([td.text_content() for td in rows['Weighted score'].xpath('td')],
                         [f"{acme.score:.2f}", f"{beta.score:.2f}"])
        self.assertEqual([td.text_content() for td in rows['Rank'].xpath('td')], ['#1', '#2'])

    def test_b_0288_a_line_left_out_reads_missing_and_the_best_price_is_marked(self):
        """Regression test for B-0288 and B-0289: a line a supplier did not quote reads Missing in its column
        and the best value of a row carries the best mark.

        Walk state: as the first test.
        Requirements: FR-SPM-0035
        """
        acme, beta, table = self._grid()
        rows = self._rows(table)
        self.assertEqual([td.text_content() for td in rows['Desk'].xpath('td')][1], "Missing")
        chair = rows['Chair'].xpath('td')
        self.assertIn('text-success', chair[0].get('class'))
        self.assertNotIn('text-success', chair[1].get('class'))

    def test_b_0289_criterion_rows_show_the_score_of_each_supplier(self):
        """Regression test for B-0289: the criterion row shows the 0-100 score of each supplier, not 0.00.

        Walk state: as the first test.
        Requirements: FR-SPM-0038
        """
        acme, beta, table = self._grid()
        cells = [td.text_content() for td in self._rows(table)['Lead time (100)'].xpath('td')]
        self.assertTrue(cells[0].startswith('100.00'))
        self.assertTrue(cells[1].startswith('0.00') or cells[1] == 'Missing')
