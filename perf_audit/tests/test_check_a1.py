from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.service.model import get_public_method

from ..checks.a1_seq_scan import SeqScanCheck
from ..checks.base import AuditContext
from .acceptance_base import AuditAcceptanceCase


def _stats(live_rows, seq_scan, idx_scan, seq_tup_read=1000):
    return {'live_rows': live_rows, 'seq_scan': seq_scan, 'idx_scan': idx_scan,
            'seq_tup_read': seq_tup_read, 'total_size': 1}


class TestA1Own(AuditAcceptanceCase):
    """Requirements: FR-OPA-0005 (T-005)."""

    def _run_a1(self, statistics):
        probe = type(self.env['perf.audit.probe'])
        with patch.object(probe, 'scan_statistics', return_value=statistics):
            return self.findings(self.start_run(), 'A1')

    def test_big_table_with_more_seq_than_index_scans(self):
        """FR-OPA-0005 / SC-OPA-0005-1: sale_order 250,000 rows, 900 seq, 100 idx gives one finding."""
        found = self._run_a1({'sale_order': _stats(250000, 900, 100, seq_tup_read=9000)})
        self.assertEqual(len(found), 1)
        self.assertEqual(found.table_name, 'sale_order')
        self.assertEqual(found.evidence['seq_scan'], 900)
        self.assertEqual(found.evidence['idx_scan'], 100)
        self.assertEqual(found.evidence['avg_rows_per_seq_scan'], 10)
        self.assertEqual(found.severity, 'medium')
        self.assertEqual(found.found_value, 250000)
        self.assertEqual(found.threshold_value, 100000)

    def test_clean_database_gives_no_finding(self):
        """FR-OPA-0005 / SC-OPA-0005-2: small tables, or index scans at least as many, give nothing."""
        found = self._run_a1({
            'small': _stats(100000, 900, 100), 'indexed': _stats(250000, 100, 100),
            'mostly_indexed': _stats(250000, 10, 900)})
        self.assertFalse(found)

    def test_model_owner_is_named(self):
        """FR-OPA-0005: the model of the table is named, a table without model has none."""
        found = self._run_a1({
            'res_partner': _stats(250000, 900, 100), 'no_model_table': _stats(250000, 900, 100)})
        self.assertEqual(found.filtered(lambda f: f.table_name == 'res_partner').model_id.model, 'res.partner')
        self.assertFalse(found.filtered(lambda f: f.table_name == 'no_model_table').model_id)

    def test_ranked_by_rows_read_by_sequential_scans(self):
        """FR-OPA-0005: the check returns the table that read most rows first."""
        statistics = {
            'few_rows_read': _stats(250000, 900, 100, seq_tup_read=10),
            'most_rows_read': _stats(250000, 900, 100, seq_tup_read=9000),
            'middle_rows_read': _stats(250000, 900, 100, seq_tup_read=500)}
        probe = type(self.env['perf.audit.probe'])
        ctx = AuditContext(
            env=self.env, probe=self.env['perf.audit.probe'], thresholds={'big_table': 100000},
            run=self.start_run())
        with patch.object(probe, 'scan_statistics', return_value=statistics):
            found = SeqScanCheck().run(ctx)
        self.assertEqual(
            [finding['table_name'] for finding in found],
            ['most_rows_read', 'middle_rows_read', 'few_rows_read'])

    def test_probe_reads_are_not_callable_remotely(self):
        """FR-OPA-0033: the statistics reads of the probe cannot be called through call_kw."""
        probe = self.env['perf.audit.probe']
        for name in ('scan_statistics', 'index_definitions'):
            with self.assertRaises(AccessError):
                get_public_method(probe, name)
