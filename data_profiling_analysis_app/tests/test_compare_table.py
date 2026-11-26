from lxml import etree

from odoo.tests import tagged
from odoo.tests.common import freeze_time
from odoo.tools.misc import format_datetime

from .common import DpaCase

MEASURE_TITLES = ("Completeness", "Empty rate", "Distinct values", "Inconsistent values", "Unusual numbers")


@tagged('post_install', '-at_install')
class TestCompareTable(DpaCase):
    """Requirements: FR-DPA-0023, FR-DPA-0025, FR-DPA-0028."""

    def _compare(self):
        """Two saved snapshots of Customer quality, a month apart, compared as a member."""
        self.env.cr.execute("UPDATE res_partner SET active = false")
        self.env.invalidate_all()
        partners = self.env['res.partner'].create([
            {'name': 'A', 'email': 'a@x.com'}, {'name': 'B'}, {'name': 'C'}, {'name': 'D'}])
        profile = self.env['dpa.profile'].create({
            'name': 'Customer quality', 'model_id': self.env['ir.model']._get('res.partner').id,
            'field_ids': [(6, 0, [self.env['ir.model.fields']._get('res.partner', 'email').id])],
        })
        snapshots = self.env['dpa.snapshot']
        for day in ('2026-11-01 09:00:00', '2026-12-01 09:00:00'):
            with freeze_time(day):
                profile.with_user(self.member).action_run()
                profile.report_id.action_save_snapshot()
                snapshots |= profile.snapshot_ids[:1]
            if day.startswith('2026-11'):
                partners[1].email = 'b@x.com'
        compare = self.env['dpa.snapshot.compare'].with_user(self.member).create({
            'snapshot_a_id': snapshots[0].id, 'snapshot_b_id': snapshots[1].id})
        compare.action_compare()
        return compare

    def _headers(self, compare):
        table = etree.fromstring('<div>%s</div>' % compare.result_html)
        return [' '.join(''.join(th.itertext()).split()) for th in table.xpath('//thead//th')], table

    def test_bug_b_0300_columns_are_titled_with_the_two_snapshot_dates(self):
        """Regression test for B-0300: each measure is titled, and its columns carry the two snapshot dates.

        Walk state: ran Customer quality on 1 Nov and 1 Dec 2026, saved both reports as snapshots, picked them in
        the comparison dialog and pressed Compare. The test does not render the dialog.
        Requirements: FR-DPA-0023
        """
        compare = self._compare()
        headers, _table = self._headers(compare)
        older = format_datetime(self.env, compare.older_id.run_date)
        newer = format_datetime(self.env, compare.newer_id.run_date)
        self.assertNotEqual(older, newer)
        for title in MEASURE_TITLES:
            self.assertIn(title, headers)
        self.assertEqual(headers.count(older), len(MEASURE_TITLES))
        self.assertEqual(headers.count(newer), len(MEASURE_TITLES))
        self.assertEqual(headers.count("Change"), len(MEASURE_TITLES))

    def test_bug_b_0301_every_measure_is_in_the_result_not_beyond_an_edge(self):
        """Regression test for B-0301: distinct, inconsistent and unusual values sit in the same result as the rest.

        Walk state: as above, then read the Email row of the result: completeness 25.0 to 50.0 (+25.0).
        The test cannot judge scrolling in a dialog; it asserts the cells exist and the 18-column list is gone from the dialog.
        Requirements: FR-DPA-0023
        """
        compare = self._compare()
        _headers, table = self._headers(compare)
        row = [' '.join(''.join(td.itertext()).split()) for td in table.xpath("//tbody/tr[1]/*")]
        self.assertEqual(row[:4], ["Email", "25.0", "50.0", "+25.0"])
        self.assertEqual(len(row), 1 + 3 * len(MEASURE_TITLES))
        arch = etree.fromstring(self.env['dpa.snapshot.compare'].get_view(view_type='form')['arch'])
        self.assertFalse(arch.xpath("//field[@name='line_ids']//field[@name='newer_unusual']"))
        self.assertTrue(arch.xpath("//field[@name='result_html']"))

    def test_bug_b_0300_field_in_one_snapshot_shows_not_profiled_without_change(self):
        """Regression test for B-0300: a field profiled in one snapshot says Not profiled and has no change.

        Walk state: compared two snapshots, then changed the older one's field list through its line (Name only).
        Requirements: FR-DPA-0025
        """
        compare = self._compare()
        Line = self.env['dpa.snapshot.compare.line']
        values = Line._line_values(1, False, compare.newer_id.field_line_ids)
        compare.line_ids.unlink()
        compare.write({'line_ids': [(0, 0, values)]})
        _headers, table = self._headers(compare)
        row = [' '.join(''.join(td.itertext()).split()) for td in table.xpath("//tbody/tr[1]/*")]
        self.assertEqual(row[1:4], ["Not profiled", "50.0", ""])
