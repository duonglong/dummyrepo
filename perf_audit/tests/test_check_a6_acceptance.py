from odoo.tests.common import tagged

from .acceptance_base import AuditAcceptanceCase


@tagged('post_install', '-at_install')
class TestA6QueriesAcceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0012, NFR-OPA-0005 (T-014).

    The test server does not preload the query statistics extension, so its view cannot be
    read for real. The fixture installs the extension (so it is listed as installed), then
    replaces its view by a plain table of the same name and columns that these tests fill.
    Everything is rolled back with the test transaction. The table names res_partner and
    res_users stand for sale_order_line of the scenario (the sale app is not a dependency).
    """

    def _fake_statements(self, rows):
        """rows: list of (query, calls, total_exec_time, mean_exec_time, other_database)."""
        cr = self.env.cr
        cr.execute('CREATE EXTENSION IF NOT EXISTS pg_stat_statements')
        cr.execute('ALTER EXTENSION pg_stat_statements DROP VIEW pg_stat_statements')
        cr.execute('DROP VIEW pg_stat_statements')
        cr.execute("""
            CREATE TABLE pg_stat_statements (
                queryid bigint, dbid oid, query text, calls bigint,
                total_exec_time double precision, mean_exec_time double precision)
        """)
        cr.execute('SELECT oid FROM pg_database WHERE datname = current_database()')
        dbid = cr.fetchone()[0]
        for index, (query, calls, total, mean, other) in enumerate(rows, start=1):
            cr.execute(
                'INSERT INTO pg_stat_statements VALUES (%s, %s, %s, %s, %s, %s)',
                [9000 + index, dbid + 1 if other else dbid, query, calls, total, mean])

    def test_top_query_shows_stats_and_model(self):
        """FR-OPA-0012 / SC-OPA-0012-1: the first A6 finding shows calls, total, mean time and the model."""
        self._fake_statements([
            ('SELECT id FROM res_users WHERE login = $1', 2, 50.0, 25.0, False),
            ('SELECT name FROM res_partner WHERE active = $1', 4, 1234.5, 308.625, False),
        ])
        run = self.start_run()
        res = self.result(run, 'A6')
        self.assertEqual(res.state, 'done')
        found = self.findings(run, 'A6')
        self.assertEqual(len(found), 2)
        first = found.sorted('id')[0]
        text = self.dump(first)
        self.assertIn('1234.5', text)
        self.assertIn('308.625', text)
        self.assertRegex(text, r'\b4\b')
        self.assertEqual(first.model_id.model, 'res.partner')
        self.assertIn('res_partner', text)

    def test_only_top_twenty_ordered_by_total_time(self):
        """FR-OPA-0012 / SC-OPA-0012-2: 50 queries: exactly 20 findings, most expensive first."""
        self._fake_statements([
            ('SELECT /* q%02d */ id FROM res_partner' % n, n, n * 10.0, 10.0, False)
            for n in range(1, 51)
        ])
        run = self.start_run()
        found = self.findings(run, 'A6').sorted('id')
        self.assertEqual(len(found), 20)
        self.assertEqual(self.result(run, 'A6').finding_count, 20)
        listed = []
        for finding in found:
            text = self.dump(finding)
            marks = [n for n in range(1, 51) if '/* q%02d */' % n in text]
            self.assertEqual(len(marks), 1, 'each finding names its query once')
            listed.append(marks[0])
        self.assertEqual(listed, list(range(50, 30, -1)))

    def test_queries_of_other_databases_are_left_out(self):
        """FR-OPA-0012: only the current database's queries are reported."""
        self._fake_statements([
            ('SELECT /* other */ id FROM res_partner', 1, 999999.0, 999999.0, True),
            ('SELECT /* mine */ id FROM res_partner', 1, 5.0, 5.0, False),
        ])
        run = self.start_run()
        found = self.findings(run, 'A6')
        self.assertEqual(len(found), 1)
        self.assertIn('/* mine */', self.dump(found))

    def test_query_text_cut_to_500_characters(self):
        """FR-OPA-0012 (context) / NFR-OPA-0005: the kept query text is at most 500 characters."""
        long_query = 'SELECT id FROM res_partner WHERE name IN (' + ', '.join(['$1'] * 400) + ')'
        self.assertGreater(len(long_query), 500)
        self._fake_statements([(long_query, 1, 10.0, 10.0, False)])
        run = self.start_run()
        found = self.findings(run, 'A6')
        self.assertEqual(len(found), 1)
        self.assertTrue(found.query_text)
        self.assertLessEqual(len(found.query_text), 500)
        self.assertEqual(found.query_id, '9001')

    def test_no_queries_gives_done_without_findings(self):
        """FR-OPA-0012: readable extension with no statements: the check is done, with no finding."""
        self._fake_statements([])
        run = self.start_run()
        res = self.result(run, 'A6')
        self.assertEqual(res.state, 'done')
        self.assertFalse(self.findings(run, 'A6'))
