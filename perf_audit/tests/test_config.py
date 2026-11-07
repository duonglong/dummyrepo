from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import convert_file, mute_logger


@tagged('post_install', '-at_install')
class TestConfig(TransactionCase):
    """Requirements: BR-OPA-0006, FR-OPA-0031 (checks and thresholds as data)."""

    def _threshold(self, key, code=None):
        domain = [('key', '=', key), ('check_id.code', '=', code)] if code else [
            ('key', '=', key), ('check_id', '=', False)]
        threshold = self.env['perf.audit.threshold'].search(domain)
        self.assertEqual(len(threshold), 1, key)
        return threshold

    def test_starting_values(self):
        """BR-OPA-0006: every starting value of the table is shipped."""
        expected = {
            (None, 'query_time_limit'): 30, (None, 'big_table'): 100000,
            (None, 'weak_evidence'): 7, ('A3', 'min_index_size'): 1,
            ('A4', 'dead_rows'): 10000, ('A4', 'dead_percent'): 20,
            ('A5', 'analyze_age'): 30, ('A5', 'modified_percent'): 20,
            ('A5', 'min_rows'): 10000, ('A6', 'top'): 20,
            ('A7', 'size'): 1024, ('A7', 'growth'): 20,
            ('A8', 'session_age'): 5, ('B2', 'fan_out'): 1000,
            ('B6', 'tracked_fields'): 10, ('B6', 'updates'): 100000,
            ('B7', 'failures'): 3, ('B7', 'lateness'): 1,
        }
        for (code, key), value in expected.items():
            self.assertEqual(self._threshold(key, code).value, value, key)
        self.assertEqual(self.env['perf.audit.threshold'].search_count([]), len(expected))

    def test_negative_and_zero_refused(self):
        """FR-OPA-0031: -5 and 0 are refused, 0.5 is kept."""
        threshold = self._threshold('big_table')
        for bad in (-5, 0):
            with self.assertRaises(ValidationError):
                threshold.write({'value': bad})
        threshold.write({'value': 0.5})
        self.assertEqual(threshold.value, 0.5)

    def test_database_constraint_refuses_non_positive(self):
        """FR-OPA-0031: the database itself refuses a non-positive value."""
        threshold = self._threshold('big_table')
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            self.env.cr.execute(
                'UPDATE perf_audit_threshold SET value = 0 WHERE id = %s', [threshold.id])

    def test_key_unique_per_check_including_global(self):
        """FR-OPA-0031: a key exists once per check, and once among global ones."""
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            self.env['perf.audit.threshold'].create({
                'key': 'big_table', 'name': 'Again', 'unit': 'rows',
                'value': 1, 'default_value': 1})

    def test_default_value_unchanged_by_edit(self):
        """FR-OPA-0031: editing the value leaves the default."""
        threshold = self._threshold('big_table')
        threshold.write({'value': 50000})
        self.assertEqual(threshold.default_value, 100000)

    def test_switch_check_off_persists(self):
        """FR-OPA-0031: a check switched off persists and stays listed."""
        check = self.env['perf.audit.check'].search([('code', '=', 'A1')])
        self.assertTrue(check.active_check)
        check.write({'active_check': False})
        check.invalidate_recordset()
        self.assertFalse(check.active_check)
        self.assertIn(check, self.env['perf.audit.check'].search([]))

    def test_check_code_unique(self):
        """BR-OPA-0006: a check code is unique."""
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            self.env['perf.audit.check'].create({'code': 'A1', 'name': 'Dup', 'category': 'database'})

    def test_upgrade_keeps_tuned_threshold(self):
        """FR-OPA-0031: reloading the shipped data (module upgrade) keeps a tuned value."""
        threshold = self._threshold('big_table')
        threshold.write({'value': 50000})
        convert_file(
            self.env, 'perf_audit', 'data/perf_audit_threshold_data.xml', {}, mode='update', kind='data')
        threshold.invalidate_recordset()
        self.assertEqual(threshold.value, 50000)

    def test_upgrade_keeps_switched_off_check_name(self):
        """FR-OPA-0031: reloading the shipped checks keeps an edited check name."""
        check = self.env['perf.audit.check'].search([('code', '=', 'A1')])
        check.write({'name': 'Renamed'})
        convert_file(
            self.env, 'perf_audit', 'data/perf_audit_check_data.xml', {}, mode='update', kind='data')
        check.invalidate_recordset()
        self.assertEqual(check.name, 'Renamed')

    def test_missing_or_empty_value_is_not_a_positive_number_error(self):
        """FR-OPA-0031: an empty value hits the required-field error, not the positive-number one."""
        base = {'key': 'x_key', 'name': 'X', 'unit': 'rows'}
        for empty in (False, None):
            with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
                self.env['perf.audit.threshold'].create({**base, 'default_value': 1, 'value': empty})
