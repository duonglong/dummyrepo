# Performance Audit (perf_audit)

Audit tool for Odoo 20 that joins PostgreSQL statistics (tables, indexes, slow
queries, sessions) with the Odoo registry, views, record rules and scheduled
actions, so each finding names its Odoo cause.

- Detection is deterministic and read-only: catalog and `pg_stat_*` queries only.
- It runs with the database rights Odoo already has; it creates no extension and
  changes no server setting. A check that needs more is reported as skipped.
- Tests: `odoo-bin -d <db> -i perf_audit --test-enable --test-tags /perf_audit`.
