from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.sql import make_index_name

from ..checks.b3_domain_index import domain_field_names, order_field_names
from .test_checks_b135_acceptance import CustomModelCase


@tagged('post_install', '-at_install')
class TestDomainParsing(TransactionCase):
    """Requirements: FR-OPA-0018, FR-OPA-0020."""

    def test_domain_leaves_give_first_path_step(self):
        """FR-OPA-0018: dotted paths count their first step; operators and logic are not fields."""
        names = domain_field_names("['|', ('state', '=', 'open'), ('partner_id.country_id', 'in', [1])]")
        self.assertEqual(names, {'state', 'partner_id'})

    def test_unparsable_domain_gives_nothing(self):
        """FR-OPA-0018: text that is not Python reports no field."""
        self.assertEqual(domain_field_names('[(('), set())

    def test_order_parts(self):
        """FR-OPA-0020: directions and extra spaces are dropped."""
        self.assertEqual(order_field_names('priority desc, id asc'), ['priority', 'id'])

    def test_values_and_any_sub_domains_are_not_leaves(self):
        """FR-OPA-0018: a 3-item value or the leaves inside an 'any' belong to no field of the outer model."""
        names = domain_field_names(
            "[('state', 'in', ('draft', 'sent', 'sale')), ('line_ids', 'any', [('qty', '>', 1)])]")
        self.assertEqual(names, {'state', 'line_ids'})


@tagged('post_install', '-at_install')
class TestB3Fix(CustomModelCase):
    """Requirements: FR-OPA-0018."""

    def test_index_name_is_the_one_odoo_gives(self):
        """FR-OPA-0018: the proposed index carries Odoo's own name for a field index."""
        self.make_x_line(rows=200000)
        self.env['ir.ui.view'].create({
            'name': 'x_line.search.name', 'model': 'x_line', 'type': 'search',
            'arch': '<search><filter name="f" string="F" domain="[(\'x_state\', \'=\', 1)]"/></search>'})
        found = self.findings(self.start_run(), 'B3').filtered(lambda f: f.field_id.name == 'x_state')
        self.assertEqual(found.index_name or found.fix_params['index_name'], make_index_name('x_line', 'x_state'))

    def test_unreadable_search_view_is_named_on_the_check_result(self):
        """FR-OPA-0018: a search view whose arch cannot be read is shown as a skipped part."""
        self.make_x_line(rows=200000)
        view = self.env['ir.ui.view'].create({
            'name': 'x_line.search.broken', 'model': 'x_line', 'type': 'search',
            'arch': '<search><filter name="f" string="F" domain="[(\'x_state\', \'=\', 1)]"/></search>'})
        with patch('odoo.addons.base.models.ir_ui_view.Base.get_views', side_effect=UserError('broken arch')):
            run = self.start_run()
        self.assertIn(str(view.id), self.result(run, 'B3').part_skip_reason)
