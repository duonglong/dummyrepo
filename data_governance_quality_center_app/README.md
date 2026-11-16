# Data Governance & Quality Center

Scans business models against quality rules in the background, scores the
result and helps correct or merge the records found.

Runs on Odoo 20.0 Community only: no Enterprise module, no external service
and no queue add-on. Background work uses Odoo's scheduled actions.

## Performance benchmark

The benchmark measures the performance targets (scan of one million records against ten rules, the time added to a
save, the memory of a scan and the opening of the dashboard). It is left out of the default test run and runs on a
database with the app installed:

    odoo-bin -d <db> -u data_governance_quality_center_app --test-tags dgq_benchmark/data_governance_quality_center_app

`DGQ_BENCHMARK_ROWS` sizes the scan (default 1000000, the target size); a smaller number gives a quick trial and the
allowed time shrinks with it.
