from unittest.mock import patch

from .acceptance_base import AuditAcceptanceCase


def _stats(live_rows, modified_rows, analyze_age):
    return {'live_rows': live_rows, 'modified_rows': modified_rows,
            'last_analyze': None if analyze_age is None else '2026-10-01 00:00:00',
            'analyze_age': analyze_age}


class TestA5Own(AuditAcceptanceCase):
    """Requirements: FR-OPA-0011 (T-006)."""

    def _run_a5(self, statistics):
        probe = type(self.env['perf.audit.probe'])
        with patch.object(probe, 'analyze_statistics', return_value=statistics):
            return self.findings(self.start_run(), 'A5')

    def test_many_modified_rows_are_reported(self):
        """FR-OPA-0011 / SC-OPA-0011-1: 50,000 rows with 15,000 modified (30 percent) give an analyze fix."""
        found = self._run_a5({'x_line': _stats(50000, 15000, 1)})
        self.assertEqual(len(found), 1)
        self.assertEqual(found.table_name, 'x_line')
        self.assertEqual(found.fix_statement, 'ANALYZE "x_line"')
        self.assertEqual(found.evidence['modified_rows'], 15000)
        self.assertEqual(found.severity, 'low')

    def test_recent_analyze_with_few_changes_is_clean(self):
        """FR-OPA-0011 / SC-OPA-0011-2: analyzed yesterday with 100 modified rows gives nothing."""
        self.assertFalse(self._run_a5({'x_line': _stats(50000, 100, 1)}))

    def test_old_analyze_needs_a_change_since(self):
        """FR-OPA-0011: an old analyze counts only when rows were modified since."""
        found = self._run_a5({
            'old_changed': _stats(50000, 1, 31), 'old_unchanged': _stats(50000, 0, 31),
            'at_age': _stats(50000, 1, 30), 'never_analyzed': _stats(50000, 5, None)})
        self.assertEqual(sorted(found.mapped('table_name')), ['never_analyzed', 'old_changed'])

    def test_percent_and_size_limits_are_strict(self):
        """FR-OPA-0011: exactly 20 percent, or 9,999 live rows, is not reported."""
        self.assertFalse(self._run_a5({
            'at_percent': _stats(50000, 10000, 1), 'small': _stats(9999, 9000, 40)}))
        found = self._run_a5({'at_min_rows': _stats(10000, 2001, 1)})
        self.assertEqual(found.table_name, 'at_min_rows')
