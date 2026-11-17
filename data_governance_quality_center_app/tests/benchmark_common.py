import os
import time

from odoo.tests import tagged

from .acceptance_m3_helpers import M3Case

# The targets of NFR-DGQ-0001 to NFR-DGQ-0004, on the reference server of 4 cores and 16 GB.
SCAN_SECONDS_PER_MILLION_RECORDS = 2 * 60 * 60
SAVE_SECONDS = 0.5
SCAN_MEMORY_MB = 500
DASHBOARD_SECONDS = 3
SNAPSHOT_DAYS = 2 * 365
BENCHMARK_RULES = 100
RECORDS_PER_MILLION = 1000000

# The run is sized by DGQ_BENCHMARK_ROWS: the default is the target size, a smaller number gives a quick trial.
BENCHMARK_ROWS = int(os.environ.get('DGQ_BENCHMARK_ROWS', RECORDS_PER_MILLION))

benchmark = tagged('-standard', '-at_install', 'post_install', 'dgq_benchmark')


class BenchmarkCase(M3Case):
    """Base class of the benchmarks: a company of its own and a timer."""

    @staticmethod
    def now():
        return time.perf_counter()

    def create_partners(self, count):
        """Insert ``count`` contacts of company X with one statement (the ORM would take hours).

        One contact in ten has no e-mail and one in twenty a malformed zip code, so every rule has
        failures to record.
        """
        self.env.cr.execute("""
            INSERT INTO res_partner (name, active, company_id, type, email, phone, street, city, zip, color,
                                     create_date, write_date)
            SELECT 'DGQ benchmark ' || n, TRUE, %s, 'contact',
                   CASE WHEN n %% 10 = 0 THEN NULL ELSE 'contact' || n || '@example.com' END,
                   '+32 2 555 ' || n, 'Street ' || n, 'City ' || (n %% 500),
                   CASE WHEN n %% 20 = 0 THEN 'x' ELSE lpad((n %% 10000)::text, 4, '0') END,
                   n %% 12, now(), now()
            FROM generate_series(1, %s) AS n
        """, (self.cx.id, count))
        self.env['res.partner'].invalidate_model()

    def standard_rules(self):
        """Ten active rules of the standard types (no custom validator) on the contacts."""
        return [
            self.make_rule('res.partner', 'required', ['email']),
            self.make_rule('res.partner', 'required', ['phone']),
            self.make_rule('res.partner', 'required', ['street']),
            self.make_rule('res.partner', 'required', ['city']),
            self.make_rule('res.partner', 'pattern', ['email'], pattern_preset='email'),
            self.make_rule('res.partner', 'pattern', ['zip'], pattern=r'[0-9]{4}'),
            self.make_rule('res.partner', 'range', ['color'], has_min=True, min_value=0, has_max=True, max_value=10),
            self.make_rule('res.partner', 'date', ['write_date'], date_condition='not_future'),
            self.make_rule(
                'res.partner', 'condition', applies_domain="[('type', '=', 'contact')]",
                condition_domain="[('name', '!=', False)]"),
            self.make_rule('res.partner', 'cross_field', ['name'], compare_field_id=self.fld('res.partner', 'city').id,
                           compare_operator='!='),
        ]
