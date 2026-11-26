from lxml import etree

from odoo.tests import tagged

from .common import DpaCase


@tagged('post_install', '-at_install')
class TestFieldDetails(DpaCase):
    """Requirements: FR-DPA-0004, FR-DPA-0015, FR-DPA-0028."""

    def test_bug_b_0299_field_details_show_completeness_filled_and_empty(self):
        """Regression test for B-0299: the details of a field show filled, empty and completeness.

        Walk state: created 10 contacts, 8 with an email, profiled Email as a member. The test reads the view of the
        field details dialog and the values it shows; it does not render it.
        Requirements: FR-DPA-0004
        """
        self.env.cr.execute("UPDATE res_partner SET active = false")
        self.env.invalidate_all()
        self.env['res.partner'].create(
            [{'name': 'P%d' % i, 'email': 'p%d@x.com' % i if i < 8 else False} for i in range(10)])
        profile = self.env['dpa.profile'].create({
            'name': 'Customer quality', 'model_id': self.env['ir.model']._get('res.partner').id,
            'field_ids': [(6, 0, [self.env['ir.model.fields']._get('res.partner', 'email').id])],
        })
        profile.with_user(self.member).action_run()
        line = profile.report_id.field_line_ids
        self.assertEqual((line.filled_count, line.empty_count, line.completeness), (8, 2, 80.0))
        arch = etree.fromstring(self.env['dpa.snapshot'].get_view(view_type='form')['arch'])
        details = arch.xpath("//field[@name='field_line_ids']/form")[0]
        for name in ('filled_count', 'empty_count', 'completeness'):
            with self.subTest(field=name):
                node = details.xpath(".//field[@name='%s']" % name)
                self.assertTrue(node)
                self.assertNotEqual(node[0].get('invisible'), '1')
