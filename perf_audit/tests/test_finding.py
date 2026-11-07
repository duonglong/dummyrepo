from odoo.tests.common import tagged

from .common import PerfAuditCase


@tagged('post_install', '-at_install')
class TestFinding(PerfAuditCase):
    """Requirements: FR-OPA-0026, FR-OPA-0027, NFR-OPA-0005."""

    def setUp(self):
        super().setUp()
        self.run = self.env['perf.audit.run'].create({})
        self.check = self.env['perf.audit.check'].search([('code', '=', 'A2')])

    def _finding(self, **values):
        base = {'run_id': self.run.id, 'check_id': self.check.id, 'identity_key': 'k',
                'severity': 'high', 'message': 'm'}
        return self.env['perf.audit.finding'].create({**base, **values})

    def test_a2_finding_shows_its_evidence(self):
        """SC-OPA-0026-1: check, severity, table, estimate, field, threshold and value found."""
        field = self.env['ir.model.fields']._get('res.partner', 'country_id')
        finding = self._finding(
            table_name='x_line', row_estimate=250000, field_id=field.id,
            threshold_label='Big table', threshold_value=100000, found_value=250000)
        self.assertEqual((finding.code, finding.severity, finding.table_name), ('A2', 'high', 'x_line'))
        self.assertEqual((finding.row_estimate, finding.threshold_value, finding.found_value),
                         (250000, 100000, 250000))
        self.assertEqual(finding.field_id, field)

    def test_create_index_statement_is_quoted(self):
        """SC-OPA-0027-1: a create-index-concurrently statement on x_line (partner_id)."""
        finding = self._finding(fix_kind='create_index', code_suggestion='Set index=True on the field',
                                fix_params={'table': 'x_line', 'columns': ['partner_id'], 'index_name': 'x_line_p_idx'})
        self.assertEqual(
            finding.fix_statement,
            'CREATE INDEX CONCURRENTLY "x_line_p_idx" ON "x_line" ("partner_id")')
        self.assertTrue(finding.can_apply)
        self.assertTrue(finding.code_suggestion)

    def test_identifier_quotes_are_escaped(self):
        """BR-OPA-0004: a quote inside a name cannot end the identifier."""
        finding = self._finding(fix_kind='analyze', fix_params={'table': 'a"; DROP TABLE b; --'})
        self.assertEqual(finding.fix_statement, 'ANALYZE "a""; DROP TABLE b; --"')

    def test_code_only_finding_has_no_statement(self):
        """SC-OPA-0027-2: a B1 finding holds a suggestion and no statement, and cannot be applied."""
        finding = self._finding(code_suggestion='Store x_margin or hide it by default in view v')
        self.assertFalse(finding.fix_statement)
        self.assertFalse(finding.can_apply)

    def test_missing_names_build_no_statement(self):
        """BR-OPA-0004: a safe kind without its names builds nothing."""
        self.assertFalse(self._finding(fix_kind='create_index', fix_params={'table': 'x_line'}).fix_statement)

    def test_autovacuum_setting_must_be_an_autovacuum_parameter(self):
        """BR-OPA-0004: only autovacuum parameters with numbers reach the statement."""
        good = self._finding(identity_key='g', fix_kind='autovacuum', fix_params={
            'table': 'x', 'settings': {'autovacuum_vacuum_scale_factor': 0.01}})
        self.assertEqual(good.fix_statement, 'ALTER TABLE "x" SET (autovacuum_vacuum_scale_factor = 0.01)')
        bad = self._finding(identity_key='b', fix_kind='autovacuum', fix_params={
            'table': 'x', 'settings': {'fillfactor); DROP TABLE x; --': 1}})
        self.assertFalse(bad.fix_statement)

    def test_query_text_is_cut_to_500_on_create_and_write(self):
        """NFR-OPA-0005: 501 characters keep the first 500; 500 stay whole."""
        finding = self._finding(query_text='q' * 501)
        self.assertEqual(finding.query_text, 'q' * 500)
        finding.query_text = 'w' * 700
        self.assertEqual(finding.query_text, 'w' * 500)
        self.assertEqual(self._finding(identity_key='e', query_text='e' * 500).query_text, 'e' * 500)
