from unittest.mock import patch

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
