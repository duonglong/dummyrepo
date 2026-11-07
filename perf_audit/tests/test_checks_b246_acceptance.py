from odoo.tests.common import tagged

from .acceptance_base import AuditAcceptanceCase


@tagged('post_install', '-at_install')
class TestB2Acceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0017, BR-OPA-0001, BR-OPA-0005 (T-009).

    x_line gets 300 real rows whose target ids repeat `per_target` times, is analyzed so the
    planner knows the distinct count, and its row estimate is then set to 500,000.
    """

    def _setup(self, distinct, rows_estimate=500000):
        model = self.make_x_line()
        self.add_field(model, 'x_target_id', 'many2one', relation='ir.model.fields',
                       on_delete='set null', index=False)
        self.add_field(
            model, 'x_label', 'char', store=True,
            compute="for rec in self:\n    rec['x_label'] = rec.x_target_id.name",
            depends='x_target_id.name')
        self.drop_plain_indexes('x_line')
        ids = self.env['ir.model.fields'].search([], limit=distinct).ids
        self.assertEqual(len(ids), distinct)
        copies = 300 // distinct
        self.env.cr.execute(
            'INSERT INTO x_line (x_target_id) SELECT t FROM unnest(%s) t, generate_series(1, %s)',
            [ids, copies])
        self.env.cr.execute('ANALYZE x_line')
        self.set_reltuples('x_line', rows_estimate)

    def _b2(self, run):
        return self.findings(run, 'B2').filtered(
            lambda f: f.field_id.model == 'x_line' and f.field_id.name == 'x_label')

    def test_fan_out_above_threshold(self):
        """FR-OPA-0017 / SC-OPA-0017-1: 500,000 rows over 100 distinct targets: fan-out 5,000, one B2 finding."""
        self._setup(distinct=100)
        found = self._b2(self.start_run())
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'medium')
        self.assertEqual(found.found_value, 5000)
        self.assertEqual(found.threshold_value, 1000)
        self.assertEqual(found.table_name, 'x_line')

    def test_unique_targets_do_not_fan_out(self):
        """FR-OPA-0017 / SC-OPA-0017-2: one row per target: no finding."""
        self._setup(distinct=300)
        run = self.start_run()
        self.assertEqual(self.result(run, 'B2').state, 'done')
        self.assertFalse(self._b2(run))

    def test_fan_out_threshold_boundary(self):
        """FR-OPA-0017: a fan-out of exactly the threshold (5,000) is not above it, 4,999 is below."""
        self._setup(distinct=100)
        self.set_threshold('fan_out', 5000, check='B2')
        self.assertFalse(self._b2(self.start_run()))
        self.set_threshold('fan_out', 4999, check='B2')
        self.assertEqual(len(self._b2(self.start_run())), 1)

    def test_small_table_is_not_reported(self):
        """FR-OPA-0017 / BR-OPA-0001: a table of 100,000 estimated rows is not big."""
        self._setup(distinct=100, rows_estimate=100000)
        self.set_threshold('fan_out', 1, check='B2')
        self.assertFalse(self._b2(self.start_run()))


@tagged('post_install', '-at_install')
class TestB4Acceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0019, BR-OPA-0005 (T-009)."""

    def _rule(self, domain, active=True, name=None):
        model = self.env['ir.model']._get('x_line')
        # Odoo 20 keeps record rules as ir.access records without a group
        return self.env['ir.access'].create({
            'name': name or 'x_line b4 %s' % domain, 'model_id': model.id,
            'operation': 'r', 'domain': domain, 'active': active})

    def _setup(self):
        line = self.make_x_line(rows=1000)
        order = self.make_model('x_order')
        self.add_field(order, 'x_partner_id', 'many2one', relation='res.partner', on_delete='set null')
        self.add_field(line, 'x_order_id', 'many2one', relation='x_order', on_delete='set null')
        self.add_field(line, 'x_company_id', 'many2one', relation='res.company', on_delete='set null')

    def _b4(self, run, rule):
        return self.findings(run, 'B4').filtered(lambda f: f.rule_id.id == rule.id)

    def test_path_through_two_relations(self):
        """FR-OPA-0019 / SC-OPA-0019-1: order_id.partner_id.country_id is reported with the path."""
        self._setup()
        rule = self._rule("[('x_order_id.x_partner_id.country_id', '=', user.country_id.id)]")
        found = self._b4(self.start_run(), rule)
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'medium')
        self.assertIn('x_order_id.x_partner_id.country_id', self.dump(found))
        self.assertEqual(found.model_id.model, 'x_line')

    def test_plain_company_rule_is_clean(self):
        """FR-OPA-0019 / SC-OPA-0019-2: company_id in the user's companies is not reported."""
        self._setup()
        rule = self._rule("[('x_company_id', 'in', company_ids)]")
        run = self.start_run()
        self.assertEqual(self.result(run, 'B4').state, 'done')
        self.assertFalse(self._b4(run, rule))

    def test_one_relation_path_is_clean(self):
        """FR-OPA-0019 boundary: a path through one relation only is below the limit."""
        self._setup()
        rule = self._rule("[('x_order_id.display_name', '!=', False)]")
        self.assertFalse(self._b4(self.start_run(), rule))

    def test_child_of_operator_is_reported(self):
        """FR-OPA-0019: a child_of operator is reported."""
        self._setup()
        rule = self._rule("[('x_partner_id', 'child_of', user.partner_id.id)]")
        found = self._b4(self.start_run(), rule)
        self.assertEqual(len(found), 1)
        self.assertIn('child_of', self.dump(found))

    def test_or_between_different_first_fields_is_reported(self):
        """FR-OPA-0019: an or between conditions on different first fields is reported."""
        self._setup()
        rule = self._rule("['|', ('x_partner_id', '=', user.partner_id.id), ('x_company_id', '=', user.company_id.id)]")
        self.assertEqual(len(self._b4(self.start_run(), rule)), 1)

    def test_or_on_the_same_first_field_is_clean(self):
        """FR-OPA-0019 boundary: an or between conditions on the same first field is not reported."""
        self._setup()
        rule = self._rule("['|', ('x_company_id', '=', False), ('x_company_id', 'in', company_ids)]")
        self.assertFalse(self._b4(self.start_run(), rule))

    def test_inactive_rule_is_ignored(self):
        """FR-OPA-0019: only active record rules are checked."""
        self._setup()
        rule = self._rule("[('x_order_id.x_partner_id.country_id', '=', 1)]", active=False)
        self.assertFalse(self._b4(self.start_run(), rule))


@tagged('post_install', '-at_install')
class TestB6Acceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0021, FR-OPA-0003 (T-009).

    The test database holds no Discuss app (the module depends on base only), so the
    run-time reading of "Discuss installed" cannot be built here (see the module note).
    """

    def test_b6_skipped_without_discuss(self):
        """FR-OPA-0021 / SC-OPA-0021-2: without the Discuss app B6 is skipped with that reason."""
        self.env.cr.execute("SELECT state FROM ir_module_module WHERE name = 'mail'")
        row = self.env.cr.fetchone()
        if row and row[0] == 'installed':
            self.skipTest('the Discuss app is installed in this database')
        run = self.start_run()
        res = self.result(run, 'B6')
        self.assertEqual(res.state, 'skipped')
        self.assertIn('discuss app not installed', (res.reason or '').lower())
        self.assertFalse(self.findings(run, 'B6'))
