import threading
import time
from unittest.mock import patch

from odoo import sql_db

from .acceptance_base import AuditAcceptanceCase


class TestA8Own(AuditAcceptanceCase):
    """Requirements: FR-OPA-0014, FR-OPA-0015 (T-007)."""

    def _run(self, sessions=(), waits=()):
        probe = type(self.env['perf.audit.probe'])
        with patch.object(probe, 'long_sessions', return_value=list(sessions)) as long_sessions, \
                patch.object(probe, 'lock_waits', return_value=list(waits)):
            run = self.start_run()
        return run, long_sessions

    def test_long_session_evidence(self):
        """FR-OPA-0014 / SC-OPA-0014-1: state, duration, application name and query start are kept."""
        session = {
            'pid': 41, 'state': 'idle in transaction', 'duration': 480.0,
            'application_name': 'odoo-cron', 'query_start': '2026-11-08 10:00:00', 'query': 'SELECT 1'}
        run, long_sessions = self._run(sessions=[session])
        long_sessions.assert_called_once_with(300)
        found = self.findings(run, 'A8', kind='long')
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'high')
        self.assertEqual(found.evidence['state'], 'idle in transaction')
        self.assertEqual(found.evidence['application_name'], 'odoo-cron')
        self.assertEqual(found.found_value, 8.0)
        self.assertEqual(found.query_text, 'SELECT 1')

    def test_no_session_gives_done_without_finding(self):
        """FR-OPA-0014 / SC-OPA-0014-2: nothing older than the session age: no long finding."""
        run, _calls = self._run()
        self.assertEqual(self.result(run, 'A8').state, 'done')
        self.assertFalse(self.findings(run, 'A8'))

    def test_lock_chain_names_waiting_blocking_and_table(self):
        """FR-OPA-0015 / SC-OPA-0015-1: waiting pid, blockers with state and duration, and the table."""
        wait = {
            'pid': 2, 'application_name': 'web', 'query': 'UPDATE res_partner SET name = 1', 'table': 'res_partner',
            'blockers': [{'pid': 1, 'state': 'idle in transaction', 'duration': 90.0}]}
        run, _calls = self._run(waits=[wait])
        found = self.findings(run, 'A8', kind='lock')
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'high')
        self.assertEqual(found.evidence['waiting_pid'], 2)
        self.assertEqual(found.evidence['blocking'], wait['blockers'])
        self.assertEqual(found.table_name, 'res_partner')
        self.assertEqual(found.model_id.model, 'res.partner')

    def test_lock_wait_without_known_table(self):
        """FR-OPA-0015: a waiter whose locked table is unknown is still reported."""
        run, _calls = self._run(waits=[{
            'pid': 2, 'application_name': '', 'query': '', 'table': None, 'blockers': None}])
        found = self.findings(run, 'A8', kind='lock')
        self.assertEqual(len(found), 1)
        self.assertFalse(found.model_id)


class TestA8Probe(AuditAcceptanceCase):
    """Requirements: FR-OPA-0014, FR-OPA-0015 (T-007): the probe reads against real sessions."""

    def _other_cursor(self):
        if not hasattr(self, '_cursors'):
            self._cursors = []
            self.addCleanup(self._close_cursors)
        cursor = sql_db.db_connect(self.env.cr.dbname).cursor()
        self._cursors.append(cursor)
        return cursor

    def _close_cursors(self):
        """Close in creation order: a waiter queued behind another one only ends once that one is closed."""
        for cursor in self._cursors:
            cursor.close()

    def _pid(self, cursor):
        cursor.execute('SELECT pg_backend_pid()')
        return cursor.fetchone()[0]

    def _drop_table(self):
        cleaner = sql_db.db_connect(self.env.cr.dbname).cursor()
        try:
            cleaner.execute('DROP TABLE IF EXISTS perf_audit_row_probe')
            cleaner.commit()
        finally:
            cleaner.close()

    def _wait_for_lock(self, cursors):
        """Start one blocked UPDATE of the probe row per cursor and give the waits time to register."""
        threads = [
            threading.Thread(target=cursor.execute, args=('UPDATE perf_audit_row_probe SET value = 2',), daemon=True)
            for cursor in cursors]
        for thread in threads:
            thread.start()
            self.addCleanup(thread.join, 10)
            time.sleep(0.5)
        time.sleep(1)

    def _row_lock_scene(self, waiters):
        self.addCleanup(self._drop_table)
        setup = self._other_cursor()
        setup.execute('CREATE TABLE perf_audit_row_probe (id integer, value integer)')
        setup.execute('INSERT INTO perf_audit_row_probe VALUES (1, 1)')
        setup.commit()
        holder = self._other_cursor()
        holder.execute('UPDATE perf_audit_row_probe SET value = 3')
        cursors = [self._other_cursor() for _index in range(waiters)]
        pids = [self._pid(cursor) for cursor in cursors]
        self._wait_for_lock(cursors)
        self.addCleanup(holder.rollback)
        return pids

    def _waits_by_pid(self):
        return {wait['pid']: wait for wait in self.env['perf.audit.probe'].lock_waits()}

    def test_row_lock_wait_names_the_table(self):
        """FR-OPA-0015: a session waiting for a row lock (two writers of one record) names the table."""
        (waiting_pid,) = self._row_lock_scene(1)
        self.assertEqual(self._waits_by_pid()[waiting_pid]['table'], 'perf_audit_row_probe')

    def test_second_row_lock_waiter_names_the_table(self):
        """FR-OPA-0015: a later waiter, queued behind the first one's tuple lock, names the table too."""
        first, second = self._row_lock_scene(2)
        waits = self._waits_by_pid()
        self.assertEqual(waits[first]['table'], 'perf_audit_row_probe')
        self.assertEqual(waits[second]['table'], 'perf_audit_row_probe')

    def test_long_sessions_lists_other_sessions_only(self):
        """FR-OPA-0014: an open transaction of another session is listed, the audit's own is not."""
        other = self._other_cursor()
        other.execute('SELECT 1')
        other_pid = self._pid(other)
        pids = [session['pid'] for session in self.env['perf.audit.probe'].long_sessions(0)]
        self.assertIn(other_pid, pids)
        self.assertNotIn(self._pid(self.env.cr), pids)
        self.assertEqual(self.env['perf.audit.probe'].long_sessions(3600), [])
