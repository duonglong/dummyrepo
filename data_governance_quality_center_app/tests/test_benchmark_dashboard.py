from .benchmark_common import BENCHMARK_RULES, DASHBOARD_SECONDS, SNAPSHOT_DAYS, BenchmarkCase, benchmark


@benchmark
class TestDashboardBenchmark(BenchmarkCase):
    """The dashboard opens in three seconds with 100 rules and two years of daily snapshots.

    Requirements: NFR-DGQ-0004
    """

    def create_snapshots(self, rules):
        """One summary row a day and one row a day for each rule, as a scan leaves them (one statement)."""
        company, model = self.cx.id, self.model('res.partner').id
        self.env.cr.execute("""
            INSERT INTO data_quality_score_snapshot (
                date, company_id, model_id, rule_id, applicable_weight, failed_weight, score, has_score,
                records_scanned, records_failing, records_error, records_warning,
                issues_open_info, issues_open_warning, issues_open_critical, create_date, write_date)
            SELECT now() - day * interval '1 day', %s, %s, rule_id, 100, day %% 40, 100 - day %% 40, TRUE,
                   1000000, 1000, 10, 100, 1, 2, 3, now(), now()
            FROM generate_series(0, %s - 1) AS day,
                 (SELECT NULL::integer AS rule_id UNION ALL SELECT unnest(%s::integer[])) AS rules
        """, (company, model, SNAPSHOT_DAYS, [rule.id for rule in rules]))
        self.env['data.quality.score.snapshot'].invalidate_model()

    def test_dashboard_with_two_years_of_snapshots(self):
        """NFR-DGQ-0004: the figures, the trend and the layout of the default widgets arrive within the target."""
        self.isolate('res.partner')
        rules = [self.make_rule('res.partner', 'required', ['email']) for _number in range(BENCHMARK_RULES)]
        self.create_snapshots(rules)
        dashboard = self.env['data.quality.dashboard'].with_user(self.viewer)
        started = self.now()
        figures = dashboard.get_figures()
        dashboard.get_trend()
        dashboard.get_layout()
        elapsed = self.now() - started
        self.assertTrue(figures['has_scan'])
        self.assertLess(elapsed, DASHBOARD_SECONDS, "The dashboard took %.2f s" % elapsed)
