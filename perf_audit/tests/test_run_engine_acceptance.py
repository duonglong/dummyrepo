import re
from datetime import timedelta
from unittest import mock

from psycopg2 import IntegrityError

from odoo import sql_db
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .acceptance_base import CHECK_CODES, AuditAcceptanceCase


@tagged('post_install', '-at_install')
class TestRunEngineAcceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0001, FR-OPA-0034, NFR-OPA-0001, FR-OPA-0033 (T-003)."""

    def test_run_lists_every_check(self):
        """FR-OPA-0001 / SC-OPA-0001-1: one run, times, user, every check with state and count."""
        run = self.start_run()
        self.assertEqual(run.state, 'done')
        self.assertTrue(run.start_date)
        self.assertTrue(run.end_date)
        self.assertGreaterEqual(run.end_date, run.start_date)
        self.assertEqual(run.user_id, self.audit_admin)
        self.assertEqual(run.trigger, 'manual')
        results = run.check_result_ids
        self.assertEqual(sorted(results.mapped('check_id.code')), sorted(CHECK_CODES))
        for res in results:
            self.assertIn(res.state, ('done', 'skipped', 'failed'))
            self.assertEqual(res.finding_count, len(self.findings(run, res.check_id.code)))

    def test_switched_off_check_is_skipped(self):
        """FR-OPA-0001 / SC-OPA-0001-2: A7 switched off is skipped with reason switched off."""
        self.env['perf.audit.check'].sudo().search([('code', '=', 'A7')]).active_check = False
        run = self.start_run()
        res = self.result(run, 'A7')
        self.assertEqual(res.state, 'skipped')
        self.assertIn('switched off', (res.reason or '').lower())
        self.assertFalse(self.findings(run, 'A7'))

    def test_second_run_refused_while_one_runs(self):
        """FR-OPA-0034 / SC-OPA-0034-1: a run started 2 minutes ago blocks a new one, named in the message."""
        Run = self.env['perf.audit.run'].sudo()
        running = Run.create({'state': 'running', 'start_date': self.now() - timedelta(minutes=2)})
        before = Run.search_count([])
        with self.assertRaises(UserError) as caught:
            self.start_run()
        self.assertIn(running.name, str(caught.exception))
        self.assertEqual(Run.search_count([]), before)

    def test_run_of_59_minutes_still_blocks(self):
        """FR-OPA-0034 boundary just below one hour: the running run is not stale yet."""
        running = self.env['perf.audit.run'].sudo().create(
            {'state': 'running', 'start_date': self.now() - timedelta(minutes=59)})
        with self.assertRaises(UserError):
            self.start_run()
        self.assertEqual(running.state, 'running')

    def test_stale_run_is_closed_as_failed(self):
        """FR-OPA-0034 / SC-OPA-0034-2: a run running for 3 hours is closed failed, a new one starts."""
        old = self.env['perf.audit.run'].sudo().create(
            {'state': 'running', 'start_date': self.now() - timedelta(hours=3)})
        new = self.start_run()
        self.assertNotEqual(new, old)
        self.assertEqual(old.state, 'failed')
        self.assertTrue(old.failure_reason)
        self.assertEqual(new.state, 'done')

    def test_run_of_61_minutes_is_stale(self):
        """FR-OPA-0034 boundary just above one hour: closed as failed."""
        old = self.env['perf.audit.run'].sudo().create(
            {'state': 'running', 'start_date': self.now() - timedelta(minutes=61)})
        self.start_run()
        self.assertEqual(old.state, 'failed')

    def test_no_audit_query_counts_business_rows(self):
        """NFR-OPA-0001: a run never counts the rows of a business table."""
        queries = []
        original = sql_db.Cursor.execute

        def spy(cursor, query, *args, **kwargs):
            queries.append(str(query))
            return original(cursor, query, *args, **kwargs)

        with mock.patch.object(sql_db.Cursor, 'execute', spy):
            self.start_run()
        pattern = re.compile(
            r'count\s*\([^)]*\)[^;]*?\bfrom\s+"?(?!pg_|information_schema|ir_|perf_audit_|res_users|res_groups)\w+',
            re.I | re.S)
        offenders = [q for q in queries if pattern.search(q)]
        self.assertEqual(offenders, [], 'the audit must not count rows of business tables')

    def test_plain_user_cannot_read_audit_records(self):
        """FR-OPA-0033 / NFR-OPA-0005: runs, results and findings are for system administrators only."""
        for model in ('perf.audit.run', 'perf.audit.check.result', 'perf.audit.finding'):
            with self.assertRaises(AccessError, msg=model):
                self.env[model].with_user(self.audit_plain).search_count([])
            self.env[model].with_user(self.audit_admin).search_count([])


@tagged('post_install', '-at_install')
class TestFindingModelAcceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0026, FR-OPA-0027, BR-OPA-0002, BR-OPA-0005, NFR-OPA-0005 (T-003)."""

    def setUp(self):
        super().setUp()
        self.run = self.env['perf.audit.run'].sudo().create({})
        self.check_a2 = self.env['perf.audit.check'].sudo().search([('code', '=', 'A2')])
        self.check_a1 = self.env['perf.audit.check'].sudo().search([('code', '=', 'A1')])

    def _finding(self, **kw):
        vals = {
            'run_id': self.run.id, 'check_id': self.check_a2.id, 'identity_key': 'k-default',
            'severity': 'high', 'message': 'a finding',
        }
        vals.update(kw)
        return self.env['perf.audit.finding'].sudo().create(vals)

    def test_finding_keeps_its_evidence(self):
        """FR-OPA-0026: check, severity, table, estimate, statistics, references, threshold and value found."""
        field = self.env['ir.model.fields']._get('res.partner', 'country_id')
        finding = self._finding(
            table_name='res_partner', row_estimate=200000, evidence={'seq_scan': 900},
            model_id=field.model_id.id, field_id=field.id, threshold_label='Big table',
            threshold_value=100000, found_value=200000)
        self.env.flush_all()
        finding.invalidate_recordset()
        self.assertEqual(finding.check_id.code, 'A2')
        self.assertEqual(finding.code, 'A2')
        self.assertEqual(finding.severity, 'high')
        self.assertEqual(finding.table_name, 'res_partner')
        self.assertEqual(finding.row_estimate, 200000)
        self.assertEqual(finding.evidence, {'seq_scan': 900})
        self.assertEqual(finding.field_id, field)
        self.assertEqual(finding.threshold_value, 100000)
        self.assertEqual(finding.found_value, 200000)
        self.assertEqual(finding.comparison_state, 'new')
        self.assertEqual(finding.apply_state, 'none')

    def test_fix_statement_create_index(self):
        """FR-OPA-0027: a create_index fix holds a create-index-concurrently statement."""
        finding = self._finding(
            fix_kind='create_index',
            fix_params={'table': 'x_line', 'columns': ['x_partner_id'], 'index_name': 'x_line_x_partner_id_idx'})
        statement = (finding.fix_statement or '').upper()
        self.assertIn('CREATE INDEX CONCURRENTLY', statement)
        self.assertIn('X_LINE', statement)
        self.assertIn('X_PARTNER_ID', statement)

    def test_fix_statement_other_safe_kinds(self):
        """FR-OPA-0027: analyze, drop_index, vacuum and autovacuum build only their own safe statement."""
        analyze = self._finding(identity_key='k1', fix_kind='analyze', fix_params={'table': 'x_line'})
        self.assertTrue((analyze.fix_statement or '').upper().startswith('ANALYZE'))
        self.assertIn('x_line', analyze.fix_statement)
        drop = self._finding(identity_key='k2', fix_kind='drop_index', fix_params={'index_name': 'x_line_idx'})
        self.assertIn('DROP INDEX', (drop.fix_statement or '').upper())
        self.assertIn('x_line_idx', drop.fix_statement)
        vac = self._finding(identity_key='k3', fix_kind='vacuum', fix_params={'table': 'x_line'})
        self.assertIn('VACUUM', (vac.fix_statement or '').upper())
        self.assertNotIn('FULL', vac.fix_statement.upper())
        auto = self._finding(
            identity_key='k4', fix_kind='autovacuum',
            fix_params={'table': 'x_line', 'settings': {'autovacuum_vacuum_scale_factor': 0.01}})
        self.assertIn('ALTER TABLE', (auto.fix_statement or '').upper())
        self.assertIn('autovacuum_vacuum_scale_factor', auto.fix_statement)

    def test_no_fix_kind_means_no_statement(self):
        """FR-OPA-0027: a finding without a database fix holds a suggestion and no statement."""
        finding = self._finding(code_suggestion='Hide the column by default')
        self.assertFalse(finding.fix_statement)
        self.assertEqual(finding.code_suggestion, 'Hide the column by default')

    def test_statement_outside_safe_list_is_refused(self):
        """FR-OPA-0027 / BR-OPA-0004: a fix kind outside the safe list cannot be stored."""
        with mute_logger('odoo.sql_db'), self.assertRaises(Exception):
            self._finding(fix_kind='drop_table', fix_params={'table': 'x_line'})

    def test_identity_is_unique_per_run(self):
        """BR-OPA-0003 / DESIGN: two findings of one run cannot share an identity key."""
        self._finding(identity_key='same')
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self._finding(identity_key='same')
        other = self.env['perf.audit.run'].sudo().create({})
        self._finding(identity_key='same', run_id=other.id)

    def test_findings_are_ordered_by_check_severity_identity(self):
        """BR-OPA-0002: order is check code, then severity (worst first), then identity."""
        self._finding(identity_key='b', severity='low')
        self._finding(identity_key='a', severity='low')
        self._finding(identity_key='c', severity='high')
        self._finding(identity_key='d', severity='medium')
        self._finding(identity_key='z', check_id=self.check_a1.id, severity='low')
        ordered = self.env['perf.audit.finding'].sudo().search([('run_id', '=', self.run.id)])
        self.assertEqual(
            [(f.check_id.code, f.severity, f.identity_key) for f in ordered],
            [('A1', 'low', 'z'), ('A2', 'high', 'c'), ('A2', 'medium', 'd'),
             ('A2', 'low', 'a'), ('A2', 'low', 'b')])

    def test_query_text_is_limited_to_500_characters(self):
        """NFR-OPA-0005: at most 500 characters of a query text are kept."""
        text = 'SELECT ' + 'x' * 700
        try:
            finding = self._finding(query_text=text)
        except Exception as error:  # a refusal is as good as a cut
            self.assertIsInstance(error, UserError)
            return
        self.assertLessEqual(len(finding.query_text or ''), 500)
        self.assertTrue((finding.query_text or '').startswith('SELECT x'))

    def test_query_text_of_exactly_500_is_kept(self):
        """NFR-OPA-0005 boundary at the limit: 500 characters are kept whole."""
        text = 'q' * 500
        finding = self._finding(query_text=text)
        self.assertEqual(finding.query_text, text)
