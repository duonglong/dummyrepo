from odoo.tests.common import tagged

from .acceptance_base import AuditAcceptanceCase


class CustomModelCase(AuditAcceptanceCase):
    """x_order (many2one to partner, one2many x_line_ids) and x_line, custom models made per test."""

    def make_orders(self, order_rows, compute_store=False):
        line = self.make_x_line()
        self.add_field(line, 'x_price', 'float')
        order = self.make_model('x_order')
        self.add_field(order, 'x_partner_id', 'many2one', relation='res.partner', on_delete='set null')
        self.add_field(line, 'x_order_id', 'many2one', relation='x_order', on_delete='cascade')
        self.add_field(order, 'x_line_ids', 'one2many', relation='x_line', relation_field='x_order_id')
        self.add_field(
            order, 'x_margin', 'float', store=compute_store,
            compute="for rec in self:\n    rec['x_margin'] = sum(rec.x_line_ids.mapped('x_price'))",
            depends='x_line_ids.x_price')
        self.drop_plain_indexes('x_order')
        self.set_reltuples('x_order', order_rows)
        return order

    def list_view(self, model, optional=None):
        attr = ' optional="%s"' % optional if optional else ''
        return self.env['ir.ui.view'].create({
            'name': 'x_order.list.acc', 'model': model, 'type': 'list',
            'arch': '<list><field name="x_partner_id"/><field name="x_margin"%s/></list>' % attr,
        })


@tagged('post_install', '-at_install')
class TestB1Acceptance(CustomModelCase):
    """Requirements: FR-OPA-0016, FR-OPA-0027 (T-008)."""

    def _b1(self, run):
        return self.findings(run, 'B1').filtered(
            lambda f: f.field_id.model == 'x_order' and f.field_id.name == 'x_margin')

    def test_visible_heavy_field_on_big_table_is_high(self):
        """FR-OPA-0016 / SC-OPA-0016-1 and FR-OPA-0027 / SC-OPA-0027-2: high, with a suggestion and no statement."""
        self.make_orders(300000)
        view = self.list_view('x_order')
        found = self._b1(self.start_run())
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'high')
        self.assertEqual(found.view_id, view)
        self.assertFalse(found.fix_statement)
        self.assertFalse(found.fix_kind)
        suggestion = found.code_suggestion or ''
        self.assertIn('x_margin', suggestion)
        self.assertIn(view.name, suggestion)

    def test_hidden_by_default_is_low(self):
        """FR-OPA-0016 / SC-OPA-0016-2: the same field hidden by default has severity low."""
        self.make_orders(300000)
        self.list_view('x_order', optional='hide')
        found = self._b1(self.start_run())
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'low')

    def test_visible_on_small_table_is_medium(self):
        """FR-OPA-0016 decision table: visible by default but the table is not big: medium."""
        self.make_orders(50)
        self.list_view('x_order')
        found = self._b1(self.start_run())
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'medium')

    def test_big_table_boundary_for_high(self):
        """FR-OPA-0016 / BR-OPA-0001: 100,000 rows is not big (medium), 100,001 is (high)."""
        self.make_orders(100000)
        self.list_view('x_order')
        self.assertEqual(self._b1(self.start_run()).severity, 'medium')
        self.set_reltuples('x_order', 100001)
        self.assertEqual(self._b1(self.start_run()).severity, 'high')

    def test_stored_fields_give_no_finding(self):
        """FR-OPA-0016 / SC-OPA-0016-3: a stored field in the view is not reported."""
        self.make_orders(300000, compute_store=True)
        self.list_view('x_order')
        run = self.start_run()
        self.assertFalse(self._b1(run))
        self.assertEqual(self.result(run, 'B1').state, 'done')


@tagged('post_install', '-at_install')
class TestB3Acceptance(CustomModelCase):
    """Requirements: FR-OPA-0018, BR-OPA-0001, BR-OPA-0005 (T-008)."""

    def _uses(self):
        model = self.env['ir.model']._get('x_line')
        # Odoo 20 keeps record rules as ir.access records without a group
        rule = self.env['ir.access'].create({
            'name': 'x_line b3 rule', 'model_id': model.id, 'operation': 'r',
            'domain': "[('x_state', '=', 'open')]"})
        view = self.env['ir.ui.view'].create({
            'name': 'x_line.search.b3', 'model': 'x_line', 'type': 'search',
            'arch': '<search><filter name="f_open" string="Open" domain="[(\'x_state\', \'=\', \'open\')]"/></search>'})
        return rule, view

    def _b3(self, run):
        return self.findings(run, 'B3').filtered(
            lambda f: f.field_id.model == 'x_line' and f.field_id.name == 'x_state')

    def test_unindexed_domain_field_lists_every_use(self):
        """FR-OPA-0018 / SC-OPA-0018-1: one finding for x_line.x_state naming the rule and the search filter."""
        self.make_x_line(rows=200000)
        rule, view = self._uses()
        found = self._b3(self.start_run())
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'medium')
        text = self.dump(found)
        self.assertTrue(str(rule.id) in text or rule.name.lower() in text)
        self.assertTrue(str(view.id) in text or view.name.lower() in text)
        self.assertEqual(found.table_name, 'x_line')

    def test_indexed_first_column_is_not_reported(self):
        """FR-OPA-0018 / SC-OPA-0018-2: state first column of an index: no finding."""
        self.make_x_line(rows=200000)
        self._uses()
        self.env.cr.execute('CREATE INDEX x_line_state_idx ON x_line (x_state)')
        self.assertFalse(self._b3(self.start_run()))

    def test_small_table_is_not_reported(self):
        """FR-OPA-0018 / BR-OPA-0001: at 100,000 rows the table is not big, so no finding."""
        self.make_x_line(rows=100000)
        self._uses()
        self.assertFalse(self._b3(self.start_run()))

    def test_default_sort_order_counts_as_a_use(self):
        """FR-OPA-0018: a field only used in the default sort order is reported too."""
        self.make_x_line(rows=200000, order='x_state')
        found = self._b3(self.start_run())
        self.assertEqual(len(found), 1)

    def test_window_action_domain_counts_as_a_use(self):
        """FR-OPA-0018: a window action domain on the field is a use."""
        self.make_x_line(rows=200000)
        action = self.env['ir.actions.act_window'].create({
            'name': 'x_line b3 action', 'res_model': 'x_line', 'domain': "[('x_state', '=', 'open')]"})
        found = self._b3(self.start_run())
        self.assertEqual(len(found), 1)
        text = self.dump(found)
        self.assertTrue(str(action.id) in text or action.name.lower() in text)


@tagged('post_install', '-at_install')
class TestB5Acceptance(CustomModelCase):
    """Requirements: FR-OPA-0020, BR-OPA-0005 (T-008)."""

    def _b5(self, run):
        return self.findings(run, 'B5').filtered(lambda f: f.table_name == 'x_line')

    def test_order_by_many2one_is_reported(self):
        """FR-OPA-0020 / SC-OPA-0020-1: default order partner_id: one B5 finding naming x_line and the many2one."""
        self.make_x_line(rows=200000, order='x_partner_id')
        found = self._b5(self.start_run())
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'medium')
        self.assertEqual(found.model_id.model, 'x_line')
        self.assertIn('x_partner_id', self.dump(found) + ' ' + (found.field_id.name or ''))

    def test_order_by_id_desc_is_clean(self):
        """FR-OPA-0020 / SC-OPA-0020-2: default order id desc: no finding."""
        self.make_x_line(rows=200000, order='id desc')
        run = self.start_run()
        self.assertEqual(self.result(run, 'B5').state, 'done')
        self.assertFalse(self._b5(run))

    def test_order_by_unindexed_stored_field_is_reported(self):
        """FR-OPA-0020: a stored field that is not first column of any index is reported."""
        self.make_x_line(rows=200000, order='x_date')
        self.assertEqual(len(self._b5(self.start_run())), 1)

    def test_order_by_indexed_stored_field_is_clean(self):
        """FR-OPA-0020: the same field with an index first column is not reported."""
        self.make_x_line(rows=200000, order='x_date')
        self.env.cr.execute('CREATE INDEX x_line_date_idx ON x_line (x_date)')
        self.assertFalse(self._b5(self.start_run()))

    def test_order_on_small_table_is_clean(self):
        """FR-OPA-0020 / BR-OPA-0001: 100,000 rows is not a big table."""
        self.make_x_line(rows=100000, order='x_partner_id')
        self.assertFalse(self._b5(self.start_run()))
