from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .test_checks_b135_acceptance import CustomModelCase


@tagged('post_install', '-at_install')
class TestB1NestedViews(CustomModelCase):
    """Requirements: FR-OPA-0016."""

    def test_field_in_nested_sub_view_is_not_counted(self):
        """FR-OPA-0016: a heavy field shown only inside a nested sub-view gives no finding for the outer view."""
        self.make_orders(300000)
        # x_line gets a stored field named like the heavy x_order.x_margin, shown only in the nested sub-view
        self.add_field(self.env['ir.model']._get('x_line'), 'x_margin', 'float')
        self.env['ir.ui.view'].create({
            'name': 'x_order.list.nested', 'model': 'x_order', 'type': 'list',
            'arch': '<list><field name="x_partner_id"/><field name="x_line_ids">'
                    '<list><field name="x_margin"/></list></field></list>'})
        run = self.start_run()
        self.assertFalse(self.findings(run, 'B1').filtered(lambda f: f.field_id.name == 'x_margin'))

    def test_unreadable_view_is_named_on_the_check_result(self):
        """FR-OPA-0016: a view whose arch cannot be read is shown as a skipped part, not silently left out."""
        self.make_orders(300000)
        view = self.list_view('x_order')
        with patch('odoo.addons.base.models.ir_ui_view.Base.get_views', side_effect=UserError('broken arch')):
            run = self.start_run()
        self.assertEqual(self.result(run, 'B1').state, 'done')
        self.assertIn(str(view.id), self.result(run, 'B1').part_skip_reason)
