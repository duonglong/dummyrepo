from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.service.model import get_public_method

from .acceptance_base import AuditAcceptanceCase


def _stats(live_rows, dead_rows):
    return {'live_rows': live_rows, 'dead_rows': dead_rows,
            'last_vacuum': None, 'last_autovacuum': '2026-11-01 02:00:00'}


class TestA4Own(AuditAcceptanceCase):
    """Requirements: FR-OPA-0009, FR-OPA-0010 (T-006)."""

    def _run_a4(self, statistics, extensions=(), dead_space=None):
        probe = type(self.env['perf.audit.probe'])
        with patch.object(probe, 'vacuum_statistics', return_value=statistics), \
                patch.object(probe, 'installed_extensions', return_value=set(extensions)), \
                patch.object(probe, 'approximate_dead_space', return_value=dead_space):
            run = self.start_run()
        return run, self.findings(run, 'A4')

    def test_dead_share_is_reported_with_one_decimal(self):
        """FR-OPA-0009 / SC-OPA-0009-1: 20,000 dead and 40,000 live rows give 33.3 percent."""
        run, found = self._run_a4({'x_line': _stats(40000, 20000)})
        self.assertEqual(len(found), 1)
        self.assertEqual(found.table_name, 'x_line')
        self.assertEqual(found.evidence['dead_pct'], 33.3)
        self.assertEqual(found.evidence['dead_rows'], 20000)
        self.assertEqual(found.evidence['live_rows'], 40000)
        self.assertEqual(found.evidence['last_autovacuum'], '2026-11-01 02:00:00')
        self.assertEqual(found.fix_kind, 'autovacuum')
        self.assertIn('autovacuum_vacuum_scale_factor = 0.01', found.fix_statement)

    def test_low_share_is_not_reported(self):
        """FR-OPA-0009 / SC-OPA-0009-2: 20,000 dead of 1,020,000 rows is 2 percent."""
        run, found = self._run_a4({'x_line': _stats(1000000, 20000)})
        self.assertFalse(found)
        self.assertEqual(self.result(run, 'A4').state, 'done')

    def test_thresholds_are_strict(self):
        """FR-OPA-0009: exactly 10,000 dead rows, or exactly 20 percent, is not reported."""
        _run, found = self._run_a4({'at_rows': _stats(10000, 10000), 'at_share': _stats(40000, 10001)})
        self.assertFalse(found.filtered(lambda f: f.table_name == 'at_rows'))
        self.assertEqual(found.table_name, 'at_share')
        _run, found = self._run_a4({'exact_share': _stats(40000, 10000)})
        self.assertFalse(found)

    def test_skipped_part_without_extension(self):
        """FR-OPA-0010 / SC-OPA-0010-1: the finding exists and the exact part says which extension is missing."""
        run, found = self._run_a4({'x_line': _stats(40000, 20000)})
        self.assertTrue(found)
        self.assertIn('pgstattuple', self.result(run, 'A4').part_skip_reason)

    def test_unreadable_extension_is_skipped(self):
        """FR-OPA-0010: an installed but unreadable extension skips the part and keeps the finding."""
        run, found = self._run_a4({'x_line': _stats(40000, 20000)}, extensions={'pgstattuple'})
        self.assertTrue(found)
        self.assertIn('not readable', self.result(run, 'A4').part_skip_reason)

    def test_approximate_dead_space_is_labelled(self):
        """FR-OPA-0010: with the extension the dead space joins the evidence as an approximate figure."""
        run, found = self._run_a4(
            {'x_line': _stats(40000, 20000)}, extensions={'pgstattuple'}, dead_space={'x_line': 4096})
        self.assertFalse(self.result(run, 'A4').part_skip_reason)
        self.assertEqual(found.evidence['dead_space_bytes_approx'], 4096)
        self.assertEqual(found.evidence['bloat_measure'], 'approximate')

    def test_no_flagged_table_skips_nothing(self):
        """FR-OPA-0010: nothing flagged means nothing to measure and nothing skipped."""
        run, _found = self._run_a4({'x_line': _stats(1000000, 20000)})
        self.assertFalse(self.result(run, 'A4').part_skip_reason)

    def test_probe_reads_are_not_callable_remotely(self):
        """FR-OPA-0033: the A4 to A7 probe reads cannot be called through call_kw."""
        probe = self.env['perf.audit.probe']
        for name in ('vacuum_statistics', 'approximate_dead_space', 'analyze_statistics',
                     'table_sizes', 'attachments_in_database'):
            with self.assertRaises(AccessError):
                get_public_method(probe, name)
