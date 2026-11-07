from unittest import mock

from psycopg2.errors import QueryCanceled

from odoo.tests.common import tagged

from ..checks import base as checks_base
from .common import PerfAuditCase


class StubCheck(checks_base.Check):
    """A check whose outcome a test chooses."""

    def __init__(self, code, findings=(), error=None, missing=None):
        self.code = code
        self._findings = list(findings)
        self._error = error
        self._missing = missing
        self.ran = False

    def needs(self, ctx):
        return self._missing

    def run(self, ctx):
        self.ran = True
        if self._error:
            raise self._error
        return self._findings


@tagged('post_install', '-at_install')
class TestRunEngine(PerfAuditCase):
    """Requirements: FR-OPA-0001, FR-OPA-0002, FR-OPA-0003, BR-OPA-0005, NFR-OPA-0001."""

    def _execute(self, stubs):
        registry = {stub.code: stub for stub in stubs}
        run = self.env['perf.audit.run'].create({'user_id': self.audit_admin.id})
        with mock.patch.object(type(run), '_check_registry', lambda self: registry):
            run._execute()
        return run

    def _result(self, run, code):
        return run.check_result_ids.filtered(lambda r: r.code == code)

    def test_switched_off_check_is_skipped_and_run_closed(self):
        """SC-OPA-0001-2: A7 off is skipped 'switched off'; the run gets an end date."""
        self.env['perf.audit.check'].search([('code', '=', 'A7')]).active_check = False
        stub = StubCheck('A7')
        run = self._execute([stub])
        result = self._result(run, 'A7')
        self.assertEqual((result.state, result.reason), ('skipped', 'Switched off'))
        self.assertFalse(stub.ran)
        self.assertEqual(run.state, 'done')
        self.assertTrue(run.end_date)
        self.assertEqual(len(run.check_result_ids), 15)

    def test_missing_input_is_skipped_with_its_name(self):
        """SC-OPA-0003-1: a check that needs an absent extension is skipped, naming it, with no finding."""
        run = self._execute([StubCheck('A6', missing='Extension pg_stat_statements is not installed')])
        result = self._result(run, 'A6')
        self.assertEqual(result.state, 'skipped')
        self.assertIn('pg_stat_statements', result.reason)
        self.assertFalse(run.finding_ids)

    def test_done_with_no_findings_is_valid(self):
        """FR-OPA-0001: a clean database gives done with 0 findings."""
        run = self._execute([StubCheck('A1')])
        self.assertEqual((self._result(run, 'A1').state, self._result(run, 'A1').finding_count), ('done', 0))

    def test_time_limit_fails_one_check_and_next_goes_on(self):
        """SC-OPA-0002-1: a cancelled query fails A1 'time limit reached'; A2 still runs."""
        second = StubCheck('A2')
        run = self._execute([StubCheck('A1', error=QueryCanceled()), second])
        failed = self._result(run, 'A1')
        self.assertEqual((failed.state, failed.reason), ('failed', 'Time limit reached'))
        self.assertTrue(second.ran)
        self.assertEqual(self._result(run, 'A2').state, 'done')

    def test_error_fails_the_check_with_its_message(self):
        """FR-OPA-0002: another error fails only its check, with the error as reason, and keeps no finding of it."""
        finding = {'identity_key': 'k', 'message': 'm'}
        failing = StubCheck('A2', findings=[finding], error=ValueError('boom'))
        run = self._execute([failing, StubCheck('A3')])
        self.assertEqual((self._result(run, 'A2').state, self._result(run, 'A2').reason), ('failed', 'boom'))
        self.assertEqual(self._result(run, 'A3').state, 'done')
        self.assertEqual(run.state, 'done')

    def test_findings_get_severity_from_the_map(self):
        """BR-OPA-0005: A2 findings are high, A8 lock findings high, B7 drift medium; the check may override."""
        run = self._execute([
            StubCheck('A2', findings=[{'identity_key': 'a', 'message': 'm'}]),
            StubCheck('A8', findings=[{'identity_key': 'l', 'message': 'm', 'kind': 'lock'}]),
            StubCheck('B7', findings=[{'identity_key': 'd', 'message': 'm', 'kind': 'late'}]),
            StubCheck('B1', findings=[{'identity_key': 'b', 'message': 'm', 'severity': 'low'}]),
        ])
        severities = {finding.code: finding.severity for finding in run.finding_ids}
        self.assertEqual(severities, {'A2': 'high', 'A8': 'high', 'B7': 'medium', 'B1': 'low'})
        self.assertEqual(run.finding_count, 4)

    def test_thresholds_are_stored_on_run_and_result(self):
        """BR-OPA-0002: the thresholds of the run are kept on the run and on each result."""
        run = self._execute([StubCheck('A3')])
        self.assertEqual(run.thresholds_used['query_time_limit'], 30)
        self.assertIn('min_index_size', self._result(run, 'A3').thresholds_used)

    def test_statement_timeout_comes_from_the_query_time_limit(self):
        """FR-OPA-0002: 30 seconds are set as 30000 ms while a check runs and put back after."""
        seen = []
        probe_model = type(self.env['perf.audit.probe'])
        original = probe_model.set_statement_timeout

        def spy(probe, milliseconds):
            seen.append(milliseconds)
            return original(probe, milliseconds)

        with mock.patch.object(probe_model, 'set_statement_timeout', spy):
            self._execute([StubCheck('A1')])
        self.assertEqual(seen, [30000])
        self.env.cr.execute("SELECT current_setting('statement_timeout')")
        self.assertNotEqual(self.env.cr.fetchone()[0], '30s')

    def test_probe_estimates_rows_without_counting(self):
        """BR-OPA-0001: the estimate comes from the catalog and follows reltuples."""
        self.env.flush_all()
        self.env.cr.execute("UPDATE pg_class SET reltuples = 123456 WHERE oid = to_regclass('perf_audit_check')")
        self.assertEqual(self.env['perf.audit.probe'].estimated_rows()['perf_audit_check'], 123456)
