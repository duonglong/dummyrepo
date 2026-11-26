import time

from odoo.tests import tagged

from .common import DpaCase

RECORDS = 100_000
FIELDS = 30
SECONDS = 60


@tagged('post_install', '-at_install')
class TestBenchmark(DpaCase):
    """Requirements: NFR-PERF-0001 (100,000 records and 30 fields within 60 seconds)."""

    def test_profile_of_100000_records_runs_within_a_minute(self):
        """Requirements: NFR-PERF-0001."""
        cr = self.env.cr
        cr.execute("UPDATE res_partner SET active = false")
        cr.execute("""
            INSERT INTO res_partner (name, active, email, phone, city, street, zip, ref, website,
                                     function, comment, lang, create_date, write_date)
            SELECT 'Bench ' || n, true,
                   CASE WHEN n %% 3 = 0 THEN NULL ELSE 'b' || n || '@example.com' END,
                   '+1 555 ' || (n %% 1000), 'City ' || (n %% 50), 'Street ' || n, (n %% 90000)::text, 'R' || n,
                   'https://b' || n || '.example.com', 'Job ' || (n %% 20), '<p>' || n || '</p>', 'en_US',
                   now() - (n %% 400) * interval '1 day', now() - (n %% 400) * interval '1 day'
            FROM generate_series(1, %s) AS n
        """, (RECORDS,))
        self.env.invalidate_all()
        profile = self.env['dpa.profile'].create({
            'name': 'Benchmark', 'model_id': self.env['ir.model']._get('res.partner').id})
        fields = profile.allowed_field_ids[:FIELDS]
        self.assertEqual(len(fields), FIELDS)
        profile.field_ids = fields
        started = time.monotonic()
        profile.with_user(self.member).action_run()
        elapsed = time.monotonic() - started
        report = self.env['dpa.snapshot'].search([('profile_id', '=', profile.id)])
        self.assertEqual(report.record_count, RECORDS)
        self.assertEqual(len(report.field_line_ids), FIELDS)
        self.assertLess(elapsed, SECONDS)
