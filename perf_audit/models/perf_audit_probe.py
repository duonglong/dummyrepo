from odoo import api, models

# Listed common values covering at least this share of the rows are every value of the column
ALL_VALUES_LISTED_SHARE = 0.999


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

    def distinct_values(self, table, column):
        """Estimated number of distinct values of a column, from the planner statistics, or None.

        When the statistics list the common values and together they cover every row, their
        number is the distinct count. Otherwise a negative statistic is a fraction of the
        estimated rows of the table.

        Requirements: FR-OPA-0017, BR-OPA-0001
        """
        self.env.cr.execute(
            """SELECT s.n_distinct,
                      COALESCE(cardinality(s.most_common_freqs), 0),
                      s.null_frac + COALESCE((SELECT sum(freq) FROM unnest(s.most_common_freqs) freq), 0)
                 FROM pg_stats s
                WHERE s.schemaname = current_schema() AND s.tablename = %s AND s.attname = %s""",
            [table, column])
        row = self.env.cr.fetchone()
        if row is None:
            return None
        n_distinct, listed_values, covered_share = row
        if n_distinct >= 0:
            return n_distinct
        if covered_share >= ALL_VALUES_LISTED_SHARE:
            return listed_values
        return -n_distinct * self.estimated_rows().get(table, 0)

    def table_updates(self):
        """Row updates per table since the statistics were reset.

        Requirements: FR-OPA-0021
        """
        self.env.cr.execute("SELECT relname, n_tup_upd FROM pg_stat_user_tables WHERE schemaname = current_schema()")
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
