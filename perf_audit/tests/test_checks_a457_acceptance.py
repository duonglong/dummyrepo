import json

from odoo.tests.common import tagged

from .acceptance_base import HUGE, AuditAcceptanceCase


@tagged('post_install', '-at_install')
class TestA4Acceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0009, FR-OPA-0010, BR-OPA-0005 (T-006).

    Dead-row counters cannot be faked in a transaction: the thresholds are lowered so the
    tables of the test database cross them, and each finding is compared with its live statistics.
    """

    def _tune(self, dead_rows, dead_percent):
        self.set_threshold('dead_rows', dead_rows, check='A4')
        self.set_threshold('dead_percent', dead_percent, check='A4')

    def test_a4_reports_tables_with_dead_rows(self):
        """FR-OPA-0009 / SC-OPA-0009-1: tables over both thresholds are reported with a safe fix."""
        self._tune(1, 0.0001)
        run = self.start_run()
        self.assertEqual(self.result(run, 'A4').state, 'done')
        found = self.findings(run, 'A4')
        self.assertTrue(found, 'the test database has tables with dead rows')
        for finding in found:
            self.assertEqual(finding.severity, 'medium')
            self.env.cr.execute(
                'SELECT n_dead_tup FROM pg_stat_user_tables WHERE relname = %s', [finding.table_name])
            self.assertGreaterEqual(self.env.cr.fetchone()[0], 1, finding.table_name)
            self.assertIn(finding.fix_kind, ('autovacuum', 'vacuum'))
            self.assertNotIn('FULL', (finding.fix_statement or '').upper())
            self.assertTrue(finding.evidence)

    def test_a4_needs_both_thresholds(self):
        """FR-OPA-0009 / SC-OPA-0009-2: many dead rows but a low share (or the reverse) is not reported."""
        self._tune(1, 100)  # a share above 100 percent cannot happen
        self.assertFalse(self.findings(self.start_run(), 'A4'))
        self._tune(HUGE, 0.0001)  # a share alone is not enough
        self.assertFalse(self.findings(self.start_run(), 'A4'))

    def test_a4_clean_database_is_done_without_finding(self):
        """FR-OPA-0009: nothing crosses the thresholds: done, zero findings."""
        self._tune(HUGE, 100)
        res = self.result(self.start_run(), 'A4')
        self.assertEqual(res.state, 'done')
        self.assertEqual(res.finding_count, 0)

    def test_exact_bloat_part_skipped_without_extension(self):
        """FR-OPA-0010 / SC-OPA-0010-1: no bloat extension: A4 findings exist, the exact part is skipped with a reason."""
        self.env.cr.execute('DROP EXTENSION IF EXISTS pgstattuple')
        self._tune(1, 0.0001)
        run = self.start_run()
        res = self.result(run, 'A4')
        self.assertEqual(res.state, 'done')
        self.assertTrue(self.findings(run, 'A4'))
        self.assertIn('pgstattuple', (res.part_skip_reason or '').lower())

    def test_exact_bloat_added_when_extension_is_there(self):
        """FR-OPA-0010: with the extension, no part is skipped and the measure used is labelled approximate."""
        self.env.cr.execute('CREATE EXTENSION IF NOT EXISTS pgstattuple')
        self._tune(1, 0.0001)
        run = self.start_run()
        res = self.result(run, 'A4')
        self.assertFalse(res.part_skip_reason)
        found = self.findings(run, 'A4')
        self.assertTrue(found)
        for finding in found:
            self.assertIn('approx', json.dumps(finding.evidence or {}).lower())


@tagged('post_install', '-at_install')
class TestA5Acceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0011, BR-OPA-0005 (T-006)."""

    def test_a5_reports_tables_modified_since_analyze(self):
        """FR-OPA-0011 / SC-OPA-0011-1: tables over the modified-percent threshold get an analyze fix."""
        self.set_threshold('min_rows', 1, check='A5')
        self.set_threshold('modified_percent', 0.0001, check='A5')
        run = self.start_run()
        self.assertEqual(self.result(run, 'A5').state, 'done')
        found = self.findings(run, 'A5')
        self.assertTrue(found, 'the test database has tables modified since their analyze')
        for finding in found:
            self.assertEqual(finding.severity, 'low')
            self.env.cr.execute(
                'SELECT n_mod_since_analyze FROM pg_stat_user_tables WHERE relname = %s', [finding.table_name])
            self.assertGreaterEqual(self.env.cr.fetchone()[0], 0)
            self.assertEqual(finding.fix_kind, 'analyze')
            self.assertTrue((finding.fix_statement or '').upper().startswith('ANALYZE'))
            self.assertIn(finding.table_name, finding.fix_statement)
            self.assertTrue(finding.evidence)

    def test_a5_recent_analyze_and_few_changes_is_clean(self):
        """FR-OPA-0011 / SC-OPA-0011-2: thresholds that no table crosses give no finding."""
        self.set_threshold('modified_percent', HUGE, check='A5')
        self.set_threshold('analyze_age', 36500, check='A5')
        run = self.start_run()
        res = self.result(run, 'A5')
        self.assertEqual(res.state, 'done')
        self.assertFalse(self.findings(run, 'A5'))

    def test_a5_ignores_tables_below_minimum_rows(self):
        """FR-OPA-0011: tables under the minimum row count are never reported."""
        self.set_threshold('min_rows', HUGE, check='A5')
        self.set_threshold('modified_percent', 0.0001, check='A5')
        self.assertFalse(self.findings(self.start_run(), 'A5'))


@tagged('post_install', '-at_install')
class TestA7Acceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0013, BR-OPA-0005 (T-006).

    ir_logging stands for the listed tables: it exists without any other app.
    """

    def _a7_ir_logging(self, run):
        return self.findings(run, 'A7').filtered(lambda f: f.table_name == 'ir_logging')

    def _grow_ir_logging(self):
        self.env.cr.execute("""
            INSERT INTO ir_logging (name, type, dbname, level, message, path, line, func)
            SELECT 'perf', 'server', 'x', 'INFO', repeat('m', 900), 'p', '1', 'f'
              FROM generate_series(1, 2000)
        """)

    def test_growth_since_previous_run(self):
        """FR-OPA-0013 / SC-OPA-0013-1: a table grown more than the growth threshold since the previous run is reported."""
        self.set_threshold('size', HUGE, check='A7')
        first = self.start_run()
        self.assertFalse(self._a7_ir_logging(first))
        self._grow_ir_logging()
        self.set_threshold('growth', 5, check='A7')
        second = self.start_run()
        found = self._a7_ir_logging(second)
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'low')
        self.assertGreater(found.found_value, 5)
        self.assertEqual(found.threshold_value, 5)
        self.assertTrue(found.evidence)

    def test_no_growth_gives_no_finding(self):
        """FR-OPA-0013 / SC-OPA-0013-2: tables under the size and growth thresholds: A7 done, no finding."""
        self.set_threshold('size', HUGE, check='A7')
        self.start_run()
        run = self.start_run()
        self.assertEqual(self.result(run, 'A7').state, 'done')
        self.assertFalse(self._a7_ir_logging(run))

    def test_size_above_threshold_is_reported(self):
        """FR-OPA-0013: a table above the size threshold is reported even without a previous run."""
        self.set_threshold('size', 0.001, check='A7')
        run = self.start_run()
        found = self._a7_ir_logging(run)
        self.assertEqual(len(found), 1)
        self.assertEqual(found.threshold_value, 0.001)

    def test_missing_tables_are_passed_over(self):
        """FR-OPA-0013: a listed table of an app not installed is passed over, A7 stays done."""
        self.set_threshold('size', 0.001, check='A7')
        run = self.start_run()
        self.assertEqual(self.result(run, 'A7').state, 'done')
        self.env.cr.execute("SELECT to_regclass('mail_message')")
        if not self.env.cr.fetchone()[0]:
            self.assertFalse(self.findings(run, 'A7').filtered(lambda f: f.table_name == 'mail_message'))
