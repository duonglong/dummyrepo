from odoo.tests.common import tagged

from .test_checks_b135_acceptance import CustomModelCase


@tagged('post_install', '-at_install')
class TestB1NestedViews(CustomModelCase):
    """Requirements: FR-OPA-0016."""

    def test_field_in_nested_sub_view_is_not_counted(self):
        """FR-OPA-0016: a heavy field inside a nested sub-view of the outer view gives no finding."""
        self.make_orders(300000)
        self.env['ir.ui.view'].create({
            'name': 'x_order.list.nested', 'model': 'x_order', 'type': 'list',
            'arch': '<list><field name="x_partner_id"/><field name="x_line_ids">'
                    '<list><field name="x_price"/></list></field></list>'})
        run = self.start_run()
        self.assertFalse(self.findings(run, 'B1').filtered(lambda f: f.field_id.name == 'x_margin'))
