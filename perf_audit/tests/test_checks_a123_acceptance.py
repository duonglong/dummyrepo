from odoo.tests.common import tagged

from .acceptance_base import HUGE, AuditAcceptanceCase


class ChecksAcceptanceBase(AuditAcceptanceCase):
    def table_owner(self, table):
        for name, model in self.env.registry.items():
            if getattr(model, '_table', None) == table and not model._abstract:
                return name
        return None


@tagged('post_install', '-at_install')
class TestA1Acceptance(ChecksAcceptanceBase):
    """Requirements: FR-OPA-0005, FR-OPA-0002, BR-OPA-0001, BR-OPA-0005 (T-005)."""

    def test_a1_reports_tables_scanned_sequentially(self):
        """FR-OPA-0005 / SC-OPA-0005-1: with every table counted big, A1 lists tables read mostly by seq scans.

        Scan counters cannot be faked in a transaction, so each finding is compared with the live
        statistics of its table.
        """
        self.set_threshold('big_table', 1)
        run = self.start_run()
        self.assertEqual(self.result(run, 'A1').state, 'done')
        found = self.findings(run, 'A1')
        self.assertTrue(found, 'the test database has tables read by sequential scans')
        for finding in found:
            self.assertEqual(finding.severity, 'medium')
            self.assertTrue(finding.table_name)
            self.env.cr.execute(
                'SELECT seq_scan, COALESCE(idx_scan, 0) FROM pg_stat_user_tables WHERE relname = %s',
                [finding.table_name])
            seq, idx = self.env.cr.fetchone()
            self.assertGreaterEqual(seq, idx, finding.table_name)
            owner = self.table_owner(finding.table_name)
            if owner:
                self.assertEqual(finding.model_id.model, owner, finding.table_name)
            self.assertTrue(finding.evidence)
            self.assertEqual(finding.stats_reset_date, run.stats_reset_date)
            self.assertTrue(finding.threshold_label)
        self.assertEqual(self.result(run, 'A1').finding_count, len(found))

    def test_a1_finds_nothing_when_no_table_is_big(self):
        """FR-OPA-0005 / SC-OPA-0005-2: no table above the big-table threshold: A1 done, no finding."""
        self.set_threshold('big_table', HUGE)
        run = self.start_run()
        res = self.result(run, 'A1')
        self.assertEqual(res.state, 'done')
        self.assertEqual(res.finding_count, 0)
        self.assertFalse(self.findings(run, 'A1'))

    def test_query_time_limit_fails_one_check_and_goes_on(self):
        """FR-OPA-0002 / SC-OPA-0002-1: A1 over the time limit is failed with that reason; A2 still runs."""
        self.set_threshold('query_time_limit', 0.001)
        run = self.start_run()
        res = self.result(run, 'A1')
        self.assertEqual(res.state, 'failed')
        self.assertIn('time limit reached', (res.reason or '').lower())
        self.assertEqual(len(run.check_result_ids), 15)
        self.assertIn(self.result(run, 'A2').state, ('done', 'skipped', 'failed'))
        self.assertEqual(run.state, 'done')


@tagged('post_install', '-at_install')
class TestA2Acceptance(ChecksAcceptanceBase):
    """Requirements: FR-OPA-0006, FR-OPA-0026, FR-OPA-0027, BR-OPA-0001, BR-OPA-0002, BR-OPA-0005 (T-005)."""

    def _a2(self, run):
        return self.findings(run, 'A2').filtered(
            lambda f: f.field_id.model == 'x_line' and f.field_id.name == 'x_partner_id')

    def test_unindexed_many2one_on_big_table(self):
        """FR-OPA-0006 / SC-OPA-0006-1 and FR-OPA-0026 / SC-OPA-0026-1, FR-OPA-0027 / SC-OPA-0027-1."""
        self.make_x_line(rows=200000)
        run = self.start_run()
        found = self._a2(run)
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'high')
        self.assertEqual(found.table_name, 'x_line')
        self.assertEqual(found.row_estimate, 200000)
        self.assertEqual(found.threshold_value, 100000)
        self.assertEqual(found.found_value, 200000)
        self.assertTrue(found.threshold_label)
        self.assertEqual(found.fix_kind, 'create_index')
        statement = found.fix_statement.upper()
        self.assertIn('CREATE INDEX CONCURRENTLY', statement)
        self.assertIn('X_LINE', statement)
        self.assertIn('X_PARTNER_ID', statement)
        self.assertIn('index', (found.code_suggestion or '').lower())
        self.assertIn('x_partner_id', (found.code_suggestion or ''))

    def test_first_column_of_an_index_is_not_reported(self):
        """FR-OPA-0006 / SC-OPA-0006-2: partner_id first column of an index: no finding."""
        self.make_x_line()
        self.env.cr.execute('CREATE INDEX x_line_p_d ON x_line (x_partner_id, x_date)')
        # CREATE INDEX resets reltuples on an empty table: set the estimate after it.
        self.set_reltuples('x_line', 200000)
        run = self.start_run()
        self.assertFalse(self._a2(run))

    def test_index_flag_is_not_reported(self):
        """FR-OPA-0006: a field with the index flag is not reported."""
        model = self.make_x_line()
        model.field_id.filtered(lambda f: f.name == 'x_partner_id').index = 'btree'
        self.set_reltuples('x_line', 200000)
        run = self.start_run()
        self.assertFalse(self._a2(run))

    def test_second_column_of_an_index_is_still_reported(self):
        """FR-OPA-0006: a column that is only the second of an index has no usable index."""
        self.make_x_line()
        self.env.cr.execute('CREATE INDEX x_line_d_p ON x_line (x_date, x_partner_id)')
        # CREATE INDEX resets reltuples on an empty table: set the estimate after it.
        self.set_reltuples('x_line', 200000)
        run = self.start_run()
        self.assertEqual(len(self._a2(run)), 1)

    def test_big_table_boundary(self):
        """BR-OPA-0001: 100,000 rows is not above the threshold, 100,001 is."""
        self.make_x_line(rows=100000)
        self.assertFalse(self._a2(self.start_run()))
        self.set_reltuples('x_line', 100001)
        self.assertEqual(len(self._a2(self.start_run())), 1)

    def test_findings_are_repeatable(self):
        """BR-OPA-0002: the same database gives the same A2 findings in the same order."""
        self.make_x_line(rows=200000)
        first = self.start_run()
        second = self.start_run()
        keys = lambda run: [(f.code, f.severity, f.identity_key) for f in self.findings(run, 'A2')]
        self.assertTrue(keys(first))
        self.assertEqual(keys(first), keys(second))

    def test_clean_database_gives_no_a2_finding_for_small_tables(self):
        """FR-OPA-0006: a small table never gets an A2 finding."""
        self.make_x_line(rows=10)
        run = self.start_run()
        self.assertEqual(self.result(run, 'A2').state, 'done')
        self.assertFalse(self._a2(run))


@tagged('post_install', '-at_install')
class TestA3Acceptance(ChecksAcceptanceBase):
    """Requirements: FR-OPA-0007, FR-OPA-0008, FR-OPA-0004, BR-OPA-0005 (T-005).

    A new empty index has one 8 KB page (0.0078125 MB) and no scan.
    """

    PAGE_MB = 8192 / 1048576

    def _unused(self, run, index):
        return self.findings(run, 'A3').filtered(lambda f: f.index_name == index and f.kind == 'unused')

    def test_unused_index_is_reported(self):
        """FR-OPA-0007 / SC-OPA-0007-1: a non-unique unused index is named with its size and the reset time."""
        self.make_x_line()
        self.env.cr.execute('CREATE INDEX x_line_unused_idx ON x_line (x_date)')
        self.set_threshold('min_index_size', 0.001, check='A3')
        run = self.start_run()
        found = self._unused(run, 'x_line_unused_idx')
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'low')
        self.assertEqual(found.table_name, 'x_line')
        self.assertTrue(run.stats_reset_date)
        self.assertEqual(found.stats_reset_date, run.stats_reset_date)
        self.assertTrue(found.evidence)
        self.assertEqual(found.fix_kind, 'drop_index')
        self.assertIn('x_line_unused_idx', found.fix_statement)

    def test_unique_index_is_never_reported(self):
        """FR-OPA-0007 / SC-OPA-0007-2: a unique index with no scan gives no finding."""
        self.make_x_line()
        self.env.cr.execute('CREATE UNIQUE INDEX x_line_unique_idx ON x_line (x_date)')
        self.set_threshold('min_index_size', 0.001, check='A3')
        run = self.start_run()
        self.assertFalse(self.findings(run, 'A3').filtered(lambda f: 'x_line_unique_idx' in self.dump(f)))

    def test_minimum_index_size_boundary(self):
        """FR-OPA-0007: an index exactly at the minimum size is reported, one just under is not."""
        self.make_x_line()
        self.env.cr.execute('CREATE INDEX x_line_size_idx ON x_line (x_date)')
        self.env.cr.execute("SELECT pg_relation_size('x_line_size_idx'::regclass)")
        self.assertEqual(self.env.cr.fetchone()[0], 8192)
        self.set_threshold('min_index_size', self.PAGE_MB, check='A3')
        self.assertEqual(len(self._unused(self.start_run(), 'x_line_size_idx')), 1)
        self.set_threshold('min_index_size', self.PAGE_MB * 1.01, check='A3')
        # FR-OPA-0030: a finding that is gone from this run is listed as 'fixed'; it is not a finding of this run.
        self.assertFalse(self._unused(self.start_run(), 'x_line_size_idx').filtered(
            lambda f: f.comparison_state != 'fixed'))

    def test_duplicate_leading_columns(self):
        """FR-OPA-0008 / SC-OPA-0008-1: (partner_id) next to (partner_id, date): one duplicate finding names both."""
        self.make_x_line()
        self.env.cr.execute('CREATE INDEX x_line_dup_short ON x_line (x_partner_id)')
        self.env.cr.execute('CREATE INDEX x_line_dup_long ON x_line (x_partner_id, x_date)')
        run = self.start_run()
        found = self.findings(run, 'A3', kind='duplicate').filtered(
            lambda f: 'x_line_dup_short' in self.dump(f))
        self.assertEqual(len(found), 1)
        self.assertIn('x_line_dup_long', self.dump(found))
        self.assertEqual(found.severity, 'low')

    def test_different_column_order_is_not_duplicate(self):
        """FR-OPA-0008 / SC-OPA-0008-2: (partner_id) and (date, partner_id) are not duplicates."""
        self.make_x_line()
        self.env.cr.execute('CREATE INDEX x_line_ord_a ON x_line (x_partner_id)')
        self.env.cr.execute('CREATE INDEX x_line_ord_b ON x_line (x_date, x_partner_id)')
        run = self.start_run()
        self.assertFalse(self.findings(run, 'A3', kind='duplicate').filtered(
            lambda f: 'x_line_ord_a' in self.dump(f) or 'x_line_ord_b' in self.dump(f)))

    def test_unique_index_is_not_proposed_for_removal_as_duplicate(self):
        """FR-OPA-0008: a unique index covered by a longer index is never reported."""
        self.make_x_line()
        self.env.cr.execute('CREATE UNIQUE INDEX x_line_uq_short ON x_line (x_partner_id)')
        self.env.cr.execute('CREATE INDEX x_line_uq_long ON x_line (x_partner_id, x_date)')
        run = self.start_run()
        self.assertFalse(self.findings(run, 'A3', kind='duplicate').filtered(
            lambda f: f.index_name == 'x_line_uq_short'))
