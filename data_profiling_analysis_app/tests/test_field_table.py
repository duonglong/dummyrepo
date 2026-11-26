from ast import literal_eval

from lxml import etree

from odoo.tests import tagged

from .common import DpaCase


@tagged('post_install', '-at_install')
class TestFieldTable(DpaCase):
    """Requirements: FR-DPA-0015, FR-DPA-0004."""

    def _report(self):
        self.env.cr.execute("UPDATE res_partner SET active = false")
        self.env.invalidate_all()
        self.env['res.partner'].create([
            {'name': 'A', 'email': 'a@x.com'}, {'name': 'B'}, {'name': 'C'}, {'name': 'D', 'email': 'd@x.com'}])
        model = self.env['ir.model']._get('res.partner')
        profile = self.env['dpa.profile'].create({
            'name': 'Customer quality', 'model_id': model.id, 'empty_threshold': 20,
            'field_ids': [(6, 0, [self.env['ir.model.fields']._get('res.partner', n).id for n in ('name', 'email')])],
        })
        profile.with_user(self.member).action_run()
        return profile.report_id

    def _filter_domain(self, name):
        view = self.env['dpa.snapshot.field'].with_user(self.member).get_view(view_type='search')
        node = etree.fromstring(view['arch']).xpath("//filter[@name='%s']" % name)
        self.assertTrue(node, "the field search offers the filter %s" % name)
        return literal_eval(node[0].get('domain'))

    def test_bug_b_0298_field_table_filters_to_flagged_fields(self):
        """Regression test for B-0298: the fields of a report can be narrowed to the flagged ones.

        Walk state: created the profile Customer quality on Name and Email with 2 of 4 emails empty, ran it as a
        member, then opened the report's Fields button and applied the Flagged filter. The test does not click.
        Requirements: FR-DPA-0015, NFR-USE-0001
        """
        report = self._report()
        action = report.with_user(self.member).action_open_fields()
        lines = self.env[action['res_model']].with_user(self.member).search(
            literal_eval(str(action['domain'])) + self._filter_domain('flagged'))
        self.assertEqual(lines.mapped('field_name'), ['email'])
        self.assertEqual(lines.flag_labels, "High empty rate")
        self.assertEqual(report.flagged_count, 1)
        self.assertEqual(action['context']['search_default_flagged'], 1)

    def test_bug_b_0298_field_table_filters_by_each_flag(self):
        """Regression test for B-0298: each flag has its own filter, so a flag is not only a text column.

        Walk state: ran a profile as a member (Email flagged High empty rate), then applied each flag filter.
        Requirements: NFR-USE-0001
        """
        report = self._report()
        Lines = self.env['dpa.snapshot.field'].with_user(self.member)
        for name, expected in (('high_empty', ['email']), ('inconsistent', []), ('unusual', [])):
            with self.subTest(filter=name):
                found = Lines.search([('snapshot_id', '=', report.id)] + self._filter_domain(name))
                self.assertEqual(found.mapped('field_name'), expected)
