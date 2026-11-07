from odoo import api, models


class PerfAuditProbe(models.AbstractModel):
    """The only place the audit issues SQL: catalog and statistics reads, with plain results.

    Every method returns plain dicts or values so a test can patch the statistics.
    Rows are never counted: estimates come from the catalog (BR-OPA-0001).

    Requirements: BR-OPA-0001, NFR-OPA-0001
    """

    _name = 'perf.audit.probe'
    _description = 'Performance audit database probe'

    def set_statement_timeout(self, milliseconds):
        """Limit every statement of the current transaction; return the previous limit.

        Requirements: FR-OPA-0002
        """
        self.env.cr.execute("SELECT current_setting('statement_timeout')")
        previous = self.env.cr.fetchone()[0]
        self.env.cr.execute("SELECT set_config('statement_timeout', %s, true)", [str(int(milliseconds))])
        return previous

    def restore_statement_timeout(self, previous):
        """Put back the limit `set_statement_timeout` returned."""
        self.env.cr.execute("SELECT set_config('statement_timeout', %s, true)", [previous])

    def estimated_rows(self):
        """Estimated live rows per table of the public schema, from the catalog statistics.

        Requirements: BR-OPA-0001
        """
        self.env.cr.execute("""
            SELECT c.relname,
                   CASE WHEN c.reltuples >= 0 THEN c.reltuples::bigint
                        ELSE COALESCE(s.n_live_tup, 0) END
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = current_schema()
         LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
             WHERE c.relkind IN ('r', 'p')
        """)
        return dict(self.env.cr.fetchall())

    def installed_extensions(self):
        """Names of the extensions installed in the database."""
        self.env.cr.execute('SELECT extname FROM pg_extension')
        return {name for (name,) in self.env.cr.fetchall()}

    @api.private
    def stats_reset(self):
        """Return `(time, source)` of the last statistics reset of this database.

        The source is 'reset' when PostgreSQL holds a reset time, else 'server_start' with the
        time the server started.

        Requirements: FR-OPA-0004
        """
        self.env.cr.execute(
            'SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()')
        row = self.env.cr.fetchone()
        if row and row[0]:
            return row[0], 'reset'
        self.env.cr.execute('SELECT pg_postmaster_start_time()')
        return self.env.cr.fetchone()[0], 'server_start'

    def _cron_execute_pending_runs(self):
        """Entry of the background action that executes started runs.

        It sits here so the scheduled audit is the only action of the run model.

        Requirements: FR-OPA-0001
        """
        self.env['perf.audit.run']._execute_pending_runs()
