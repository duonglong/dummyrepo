import logging

from psycopg2 import Error

from odoo import api, models

_logger = logging.getLogger(__name__)

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

    @api.private
    def scan_statistics(self):
        """Live rows, scan counts, rows read by sequential scans and total size, per table of the public schema.

        Requirements: FR-OPA-0005, NFR-OPA-0001
        """
        self.env.cr.execute("""
            SELECT relname, n_live_tup, seq_scan, COALESCE(idx_scan, 0), seq_tup_read,
                   pg_total_relation_size(relid)
              FROM pg_stat_user_tables
             WHERE schemaname = current_schema()
        """)
        return {
            table: {
                'live_rows': live_rows, 'seq_scan': seq_scan, 'idx_scan': idx_scan,
                'seq_tup_read': seq_tup_read, 'total_size': total_size,
            }
            for table, live_rows, seq_scan, idx_scan, seq_tup_read, total_size in self.env.cr.fetchall()
        }

    @api.private
    def index_definitions(self):
        """The valid indexes of the public schema with their key columns, kind, size and scan count.

        `columns` lists the key columns in order (an expression is given as its text),
        `constraint` tells that the index backs a constraint and `predicate` is the
        condition of a partial index, or an empty string.

        Requirements: FR-OPA-0006, FR-OPA-0007, FR-OPA-0008, NFR-OPA-0001
        """
        self.env.cr.execute("""
            SELECT ic.relname, tc.relname,
                   ARRAY(SELECT pg_get_indexdef(i.indexrelid, k, true)
                           FROM generate_series(1, i.indnkeyatts) k ORDER BY k),
                   i.indisunique, i.indisprimary,
                   EXISTS (SELECT 1 FROM pg_constraint WHERE conindid = i.indexrelid),
                   COALESCE(pg_get_expr(i.indpred, i.indrelid), ''),
                   am.amname, pg_relation_size(i.indexrelid), COALESCE(s.idx_scan, 0)
              FROM pg_index i
              JOIN pg_class ic ON ic.oid = i.indexrelid
              JOIN pg_class tc ON tc.oid = i.indrelid
              JOIN pg_namespace n ON n.oid = tc.relnamespace AND n.nspname = current_schema()
              JOIN pg_am am ON am.oid = ic.relam
         LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = i.indexrelid
             WHERE i.indisvalid
        """)
        keys = ('name', 'table', 'columns', 'unique', 'primary', 'constraint',
                'predicate', 'method', 'size', 'idx_scan')
        return [dict(zip(keys, row)) for row in self.env.cr.fetchall()]

    @api.private
    def vacuum_statistics(self):
        """Live and dead rows and the last vacuum times, per table of the public schema.

        Requirements: FR-OPA-0009, NFR-OPA-0001
        """
        self.env.cr.execute("""
            SELECT relname, n_live_tup, n_dead_tup, last_vacuum::text, last_autovacuum::text
              FROM pg_stat_user_tables
             WHERE schemaname = current_schema()
        """)
        keys = ('live_rows', 'dead_rows', 'last_vacuum', 'last_autovacuum')
        return {table: dict(zip(keys, values)) for table, *values in self.env.cr.fetchall()}

    @api.private
    def analyze_statistics(self):
        """Live rows, rows modified since the latest analyze and its age in days, per table.

        `analyze_age` is None for a table never analyzed.

        Requirements: FR-OPA-0011, NFR-OPA-0001
        """
        self.env.cr.execute("""
            SELECT relname, n_live_tup, n_mod_since_analyze,
                   GREATEST(last_analyze, last_autoanalyze)::text,
                   extract(epoch FROM now() - GREATEST(last_analyze, last_autoanalyze)) / 86400
              FROM pg_stat_user_tables
             WHERE schemaname = current_schema()
        """)
        keys = ('live_rows', 'modified_rows', 'last_analyze', 'analyze_age')
        return {table: dict(zip(keys, values)) for table, *values in self.env.cr.fetchall()}

    @api.private
    def approximate_dead_space(self, tables):
        """Dead space in bytes of each table, from the approximate row-level measure, or None when unreadable.

        The measure reads the visibility map and only the pages it does not cover, never the whole table.

        Requirements: FR-OPA-0010, NFR-OPA-0001
        """
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute("""
                    SELECT t.name, a.dead_tuple_len::bigint
                      FROM unnest(%s::text[]) AS t(name)
                     CROSS JOIN LATERAL pgstattuple_approx(t.name::regclass) a
                """, [list(tables)])
                return dict(self.env.cr.fetchall())
        except Error:
            _logger.info('The approximate dead space of the tables could not be read', exc_info=True)
            return None

    @api.private
    def table_sizes(self, tables):
        """Total size in bytes, indexes and toast included, of each listed table that exists.

        Requirements: FR-OPA-0013, NFR-OPA-0001
        """
        self.env.cr.execute("""
            SELECT t.name, pg_total_relation_size(to_regclass(quote_ident(t.name)))
              FROM unnest(%s::text[]) AS t(name)
             WHERE to_regclass(quote_ident(t.name)) IS NOT NULL
        """, [list(tables)])
        return dict(self.env.cr.fetchall())

    @api.private
    def attachments_in_database(self):
        """True when some attachment keeps its file in the database, False when none does, None when unknown.

        Read from the planner statistics of the file column, never from the rows (NFR-OPA-0001).

        Requirements: FR-OPA-0013, NFR-OPA-0001
        """
        self.env.cr.execute(
            """SELECT null_frac < 1 FROM pg_stats
                WHERE schemaname = current_schema() AND tablename = 'ir_attachment' AND attname = 'db_datas'""")
        row = self.env.cr.fetchone()
        return row[0] if row else None

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
