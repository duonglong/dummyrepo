from unittest.mock import patch

from .acceptance_base import AuditAcceptanceCase

MEGABYTE = 1024 * 1024


class TestA7Own(AuditAcceptanceCase):
    """Requirements: FR-OPA-0013 (T-006)."""

    def _run_a7(self, sizes, files_in_database=False):
        probe = type(self.env['perf.audit.probe'])
        with patch.object(probe, 'table_sizes', return_value=sizes), \
                patch.object(probe, 'attachments_in_database', return_value=files_in_database):
            run = self.start_run()
        return run, self.findings(run, 'A7')

    def test_growth_of_forty_percent(self):
        """FR-OPA-0013 / SC-OPA-0013-1: 500 MB then 700 MB gives one finding with both sizes and 40 percent."""
        self._run_a7({'mail_message': 500 * MEGABYTE})
        run, found = self._run_a7({'mail_message': 700 * MEGABYTE})
        self.assertEqual(len(found), 1)
        self.assertEqual(found.table_name, 'mail_message')
        self.assertEqual(found.evidence['previous_size_bytes'], 500 * MEGABYTE)
        self.assertEqual(found.evidence['size_bytes'], 700 * MEGABYTE)
        self.assertEqual(found.evidence['growth_pct'], 40.0)
        self.assertEqual(found.severity, 'low')
        self.assertEqual(found.threshold_label, 'growth')
        self.assertEqual(found.found_value, 40)

    def test_absent_tables_leave_a_clean_done_check(self):
        """FR-OPA-0013 / SC-OPA-0013-2: no mail_message and every other table small: done, no finding."""
        run, found = self._run_a7({'ir_logging': 10 * MEGABYTE, 'bus_bus': 10 * MEGABYTE})
        self.assertFalse(found)
        self.assertEqual(self.result(run, 'A7').state, 'done')

    def test_sizes_are_saved_on_the_result(self):
        """FR-OPA-0013: every measured table is saved as table to bytes for the next run."""
        run, _found = self._run_a7({'ir_logging': 10 * MEGABYTE})
        self.assertEqual(self.result(run, 'A7').measures, {'ir_logging': 10 * MEGABYTE})

    def test_growth_and_size_limits_are_strict(self):
        """FR-OPA-0013: exactly 1024 MB, or exactly 20 percent growth, is not reported."""
        self._run_a7({'bus_bus': 500 * MEGABYTE, 'mail_mail': 1024 * MEGABYTE})
        _run, found = self._run_a7({'bus_bus': 600 * MEGABYTE, 'mail_mail': 1024 * MEGABYTE})
        self.assertFalse(found)
        _run, found = self._run_a7({'bus_bus': 1024 * MEGABYTE + 1})
        self.assertEqual(found.table_name, 'bus_bus')
        self.assertEqual(found.threshold_label, 'size')

    def test_size_wins_over_growth_and_reads_previous_done_run_only(self):
        """FR-OPA-0013: the size threshold labels a table over both; a first run has no growth."""
        run, found = self._run_a7({'bus_bus': 2000 * MEGABYTE})
        self.assertEqual(found.threshold_label, 'size')
        self.assertIsNone(found.evidence['growth_pct'])

    def test_attachments_in_database_are_noted(self):
        """FR-OPA-0013: an attachment table with files in the database says so, and only that table."""
        _run, found = self._run_a7(
            {'ir_attachment': 2000 * MEGABYTE, 'bus_bus': 2000 * MEGABYTE}, files_in_database=True)
        by_table = {finding.table_name: finding for finding in found}
        self.assertTrue(by_table['ir_attachment'].evidence['files_stored_in_database'])
        self.assertNotIn('files_stored_in_database', by_table['bus_bus'].evidence)
