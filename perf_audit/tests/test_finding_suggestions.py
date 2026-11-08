from unittest.mock import patch

from odoo.tests.common import tagged

from ..checks import b6_tracking
from .acceptance_base import AuditAcceptanceCase


@tagged('post_install', '-at_install')
class TestProposedFix(AuditAcceptanceCase):
    """Requirements: FR-OPA-0027, US-OPA-004 (B-0118).

    Checks without a database statement give a written suggestion that names the exact object.
    """

    def _probe(self):
        return type(self.env['perf.audit.probe'])

    def test_bug_b_0118_a1_names_the_table_and_the_checks_to_read(self):
        """FR-OPA-0027: A1 suggests finding the column through A2, B3 or A6, naming the table."""
        stats = {'live_rows': 250000, 'seq_scan': 900, 'idx_scan': 100, 'seq_tup_read': 9000, 'total_size': 1}
        with patch.object(self._probe(), 'scan_statistics', return_value={'sale_order': stats}):
            found = self.findings(self.start_run(), 'A1')
        self.assertIn('sale_order', found.code_suggestion or '')
        self.assertIn('B3', found.code_suggestion)

    def test_bug_b_0118_a6_links_to_the_checks_and_names_the_models(self):
        """FR-OPA-0027: A6 suggests reading A1, A2 and B3 for the models its query names."""
        statement = {'query_id': '1', 'query': 'SELECT id FROM res_partner', 'calls': 1,
                     'total_time': 10.0, 'mean_time': 10.0}
        probe = self._probe()
        with patch.object(probe, 'installed_extensions', return_value={'pg_stat_statements'}), \
                patch.object(probe, 'statements_readable', return_value=True), \
                patch.object(probe, 'top_statements', return_value=[statement]):
            found = self.findings(self.start_run(), 'A6')
        self.assertIn('res.partner', found.code_suggestion or '')
        self.assertIn('A1', found.code_suggestion)

    def test_bug_b_0118_b6_names_the_model(self):
        """FR-OPA-0027: B6 suggests trimming the tracked fields of the model."""
        tracked = {'res.partner': ['f%d' % number for number in range(14)]}
        check = b6_tracking.TrackingCheck
        with patch.object(check, 'needs', return_value=None), \
                patch.object(check, 'tracked_fields', return_value=tracked), \
                patch.object(self._probe(), 'table_updates', return_value={'res_partner': 300000}):
            found = self.findings(self.start_run(), 'B6')
        self.assertIn('res.partner', found.code_suggestion or '')

    def test_bug_b_0118_b2_names_the_field_and_the_link(self):
        """FR-OPA-0027: B2 suggests narrowing the dependency, naming the field and its many2one."""
        model = self.make_x_line()
        self.add_field(model, 'x_target_id', 'many2one', relation='ir.model.fields',
                       on_delete='set null', index=False)
        self.add_field(
            model, 'x_label', 'char', store=True,
            compute="for rec in self:\n    rec['x_label'] = rec.x_target_id.name",
            depends='x_target_id.name')
        ids = self.env['ir.model.fields'].search([], limit=100).ids
        self.env.cr.execute(
            'INSERT INTO x_line (x_target_id) SELECT t FROM unnest(%s) t, generate_series(1, 3)', [ids])
        self.env.cr.execute('ANALYZE x_line')
        self.set_reltuples('x_line', 500000)
        found = self.findings(self.start_run(), 'B2').filtered(lambda f: f.field_id.name == 'x_label')
        self.assertIn('x_label', found.code_suggestion or '')
        self.assertIn('x_target_id', found.code_suggestion)

    def test_bug_b_0118_b4_names_the_rule_and_the_path(self):
        """FR-OPA-0027: B4 suggests storing the path as an indexed related field, naming the rule."""
        line = self.make_x_line(rows=1000)
        order = self.make_model('x_order')
        self.add_field(order, 'x_partner_id', 'many2one', relation='res.partner', on_delete='set null')
        self.add_field(line, 'x_order_id', 'many2one', relation='x_order', on_delete='set null')
        rule = self.env['ir.access'].create({
            'name': 'x_line b4 suggestion', 'model_id': self.env['ir.model']._get('x_line').id,
            'operation': 'r', 'domain': "[('x_order_id.x_partner_id.country_id', '=', 1)]"})
        found = self.findings(self.start_run(), 'B4').filtered(lambda f: f.rule_id == rule)
        self.assertIn('x_order_id.x_partner_id.country_id', found.code_suggestion or '')
        self.assertIn('x_line b4 suggestion', found.code_suggestion)
