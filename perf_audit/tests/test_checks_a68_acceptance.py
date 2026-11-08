import threading
import time

from odoo import sql_db
from odoo.tests.common import tagged

from .acceptance_base import AuditAcceptanceCase


@tagged('post_install', '-at_install')
class TestA6Acceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0003, FR-OPA-0012, BR-OPA-0005 (T-007).

    The test server does not preload the statistics extension, so only the skipped path
    of A6 can be exercised (see the module note).
    """

    def test_a6_skipped_without_extension(self):
        """FR-OPA-0003 / SC-OPA-0003-1: no extension: A6 skipped naming it, with no finding."""
        self.env.cr.execute('DROP EXTENSION IF EXISTS pg_stat_statements')
        run = self.start_run()
        res = self.result(run, 'A6')
        self.assertEqual(res.state, 'skipped')
        self.assertIn('pg_stat_statements', (res.reason or '').lower())
        self.assertFalse(self.findings(run, 'A6'))
        self.assertEqual(res.finding_count, 0)

    def test_skipped_check_does_not_stop_the_run(self):
        """FR-OPA-0003: the skipped check is still listed and the other checks ran."""
        run = self.start_run()
        self.assertEqual(len(run.check_result_ids), 15)
        self.assertEqual(run.state, 'done')


@tagged('post_install', '-at_install')
class TestA8Acceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0014, FR-OPA-0015, NFR-OPA-0005, BR-OPA-0005 (T-007)."""

    def _other_cursor(self):
        cursor = sql_db.db_connect(self.env.cr.dbname).cursor()
        self.addCleanup(cursor.close)
        return cursor

    def test_idle_in_transaction_session_is_reported(self):
        """FR-OPA-0014 / SC-OPA-0014-1: another session idle in transaction past the session age is reported."""
        other = self._other_cursor()
        other.execute("SET application_name = 'perf_audit_idle_probe'")
        other.execute('SELECT 1')
        other.execute('SELECT pg_backend_pid()')
        pid = other.fetchone()[0]
        time.sleep(1.5)
        self.set_threshold('session_age', 0.01, check='A8')  # 0.6 second
        run = self.start_run()
        found = self.findings(run, 'A8', kind='long').filtered(
            lambda f: 'perf_audit_idle_probe' in self.dump(f))
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'high')
        text = self.dump(found)
        self.assertIn('idle in transaction', text)
        self.assertIn(str(pid), text)
        self.assertLessEqual(len(found.query_text or ''), 500)
        self.assertEqual(self.result(run, 'A8').state, 'done')

    def test_young_session_is_not_reported(self):
        """FR-OPA-0014 / SC-OPA-0014-2: a transaction younger than the default 5 minutes gives no long finding."""
        other = self._other_cursor()
        other.execute("SET application_name = 'perf_audit_young_probe'")
        other.execute('SELECT 1')
        run = self.start_run()
        self.assertFalse(self.findings(run, 'A8', kind='long').filtered(
            lambda f: 'perf_audit_young_probe' in self.dump(f)))

    def test_own_session_is_never_reported(self):
        """FR-OPA-0014: the audit's own session is left out even with a tiny session age."""
        self.set_threshold('session_age', 0.0001, check='A8')
        run = self.start_run()
        self.env.cr.execute('SELECT pg_backend_pid()')
        own = str(self.env.cr.fetchone()[0])
        for finding in self.findings(run, 'A8', kind='long'):
            self.assertNotEqual(str((finding.evidence or {}).get('pid')), own)

    def test_lock_chain_is_reported(self):
        """FR-OPA-0015 / SC-OPA-0015-1: a session waiting on a table lock names the blocking session and the table."""
        setup = self._other_cursor()
        self.addCleanup(self._drop_probe)
        setup.execute('CREATE TABLE perf_audit_lock_probe (id integer)')
        setup.commit()
        holder = self._other_cursor()
        holder.execute('LOCK TABLE perf_audit_lock_probe IN ACCESS EXCLUSIVE MODE')
        holder.execute('SELECT pg_backend_pid()')
        blocking_pid = holder.fetchone()[0]
        waiter = self._other_cursor()
        waiter.execute("SET application_name = 'perf_audit_lock_waiter'")
        waiter.execute('SELECT pg_backend_pid()')
        waiting_pid = waiter.fetchone()[0]
        thread = threading.Thread(
            target=lambda: waiter.execute('SELECT * FROM perf_audit_lock_probe'), daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(holder.rollback)
        time.sleep(1.5)
        run = self.start_run()
        found = self.findings(run, 'A8', kind='lock').filtered(
            lambda f: 'perf_audit_lock_probe' in self.dump(f))
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'high')
        text = self.dump(found)
        self.assertIn(str(waiting_pid), text)
        self.assertIn(str(blocking_pid), text)

    def test_no_waiting_session_gives_no_lock_finding(self):
        """FR-OPA-0015 / SC-OPA-0015-2: with no session waiting for a lock, no lock finding is listed."""
        run = self.start_run()
        self.assertEqual(self.result(run, 'A8').state, 'done')
        self.assertFalse(self.findings(run, 'A8', kind='lock'))

    def _drop_probe(self):
        cleaner = sql_db.db_connect(self.env.cr.dbname).cursor()
        try:
            cleaner.execute('DROP TABLE IF EXISTS perf_audit_lock_probe')
            cleaner.commit()
        finally:
            cleaner.close()
