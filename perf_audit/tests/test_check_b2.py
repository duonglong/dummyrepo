from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestProbeDistinct(TransactionCase):
    """Requirements: FR-OPA-0017"""

    def test_negative_statistic_is_a_fraction_of_rows(self):
        """FR-OPA-0017: n_distinct -0.5 on 1,000 estimated rows is 500 distinct values."""
        probe = self.env['perf.audit.probe']
        with patch.object(type(probe), 'estimated_rows', return_value={'res_partner': 1000}):
            self.env.cr.execute("ANALYZE res_partner")
            self.env.cr.execute(
                "SELECT n_distinct FROM pg_stats WHERE tablename = 'res_partner' AND attname = 'id'")
            self.assertEqual(self.env.cr.fetchone()[0], -1)
            self.assertEqual(probe.distinct_values('res_partner', 'id'), 1000)

    def test_unknown_column_has_no_statistic(self):
        """FR-OPA-0017: a column without statistics gives None, so no finding is built on a guess."""
        self.assertIsNone(self.env['perf.audit.probe'].distinct_values('res_partner', 'no_such_column'))

    def test_listed_common_values_give_the_exact_distinct_count(self):
        """FR-OPA-0017: 100 values over 300 rows (statistic -1/3) are all listed, so 100 is the distinct count."""
        self.env.cr.execute(
            'CREATE TABLE perf_audit_b2_probe AS SELECT g % 100 AS target FROM generate_series(1, 300) g')
        self.env.cr.execute('ANALYZE perf_audit_b2_probe')
        probe = self.env['perf.audit.probe']
        self.assertEqual(probe.distinct_values('perf_audit_b2_probe', 'target'), 100)
