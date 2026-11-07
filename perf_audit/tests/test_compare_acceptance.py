"""Acceptance tests of T-012: compare a finished run with the previous ones.

Requirements: BR-OPA-0003, FR-OPA-0030
"""
from datetime import timedelta
from unittest import mock

from odoo.tests.common import tagged

from .acceptance_base import CHECK_CODES, AuditAcceptanceCase


class _Stub:
    """A check that returns the findings a test chose (or is skipped, or fails)."""

    def __init__(self, findings=(), missing=None, error=None):
        self.findings = list(findings)
        self.missing = missing
        self.error = error

    def needs(self, ctx):
        return self.missing

    def run(self, ctx):
        if self.error:
            raise self.error
        return [dict(f) for f in self.findings]


def _finding(key, **extra):
    return dict({'identity_key': key, 'message': 'Finding %s' % key}, **extra)


@tagged('post_install', '-at_install')
class TestCompareAcceptance(AuditAcceptanceCase):
    """Requirements: BR-OPA-0003, FR-OPA-0030 (SC-OPA-0030-1 to SC-OPA-0030-3)."""

    A2 = 'A2:x.line.partner_id'
    A5 = 'A5:x_line'
    A1 = 'A1:sale_order'
    A6 = 'A6:query-1'

    def execute(self, hours_ago, stubs=None):
        """Create a run started `hours_ago` hours ago, execute it with stub checks, return it."""
        registry = {code: _Stub() for code in CHECK_CODES}
        registry.update(stubs or {})
        Run = self.env['perf.audit.run'].sudo()
        run = Run.create({
            'user_id': self.audit_admin.id,
            'start_date': self.now() - timedelta(hours=hours_ago),
        })
        with mock.patch.object(type(run), '_check_registry', lambda self: registry):
            run._execute()
        self.env.flush_all()
        self.assertEqual(run.state, 'done')
        return run

    def mark(self, run, identity_key):
        found = run.finding_ids.filtered(lambda f: f.identity_key == identity_key)
        self.assertEqual(len(found), 1, '%s must be listed once in the run' % identity_key)
        return found.comparison_state

    def test_first_run_marks_everything_new(self):
        """FR-OPA-0030: the first run marks every finding new and lists nothing as fixed."""
        run = self.execute(3, {'A2': _Stub([_finding(self.A2)]), 'A1': _Stub([_finding(self.A1)])})
        self.assertEqual(self.mark(run, self.A2), 'new')
        self.assertEqual(self.mark(run, self.A1), 'new')
        self.assertFalse(run.finding_ids.filtered(lambda f: f.comparison_state == 'fixed'))
        self.assertEqual((run.new_count, run.fixed_count, run.regressed_count), (2, 0, 0))

    def test_open_new_and_fixed_against_previous_run(self):
        """SC-OPA-0030-1: still open, new and fixed against the previous run."""
        previous = self.execute(2, {'A2': _Stub([_finding(self.A2)]), 'A5': _Stub([_finding(self.A5)])})
        run = self.execute(1, {'A2': _Stub([_finding(self.A2)]), 'A1': _Stub([_finding(self.A1)])})
        self.assertEqual(run.previous_run_id, previous)
        self.assertEqual(self.mark(run, self.A2), 'open')
        self.assertEqual(self.mark(run, self.A1), 'new')
        self.assertEqual(self.mark(run, self.A5), 'fixed')
        self.assertEqual((run.new_count, run.fixed_count, run.regressed_count), (1, 1, 0))
        self.assertEqual(run.finding_count, 2, 'the count leaves out the rows marked fixed')

    def test_identity_ignores_statistic_values(self):
        """BR-OPA-0003: the same check and object is the same finding although its numbers moved."""
        self.execute(2, {'A2': _Stub([_finding(self.A2, found_value=100, row_estimate=1000)])})
        run = self.execute(1, {'A2': _Stub([_finding(self.A2, found_value=900, row_estimate=9000)])})
        self.assertEqual(self.mark(run, self.A2), 'open')

    def test_same_object_under_another_check_is_another_finding(self):
        """BR-OPA-0003: the check code is part of the identity."""
        self.execute(2, {'A1': _Stub([_finding('X:x_line')])})
        run = self.execute(1, {'A2': _Stub([_finding('X:x_line')])})
        states = sorted(f.comparison_state for f in run.finding_ids if f.identity_key == 'X:x_line')
        self.assertEqual(states, ['fixed', 'new'])

    def test_regressed_when_found_before_the_previous_run(self):
        """SC-OPA-0030-2: absent from the previous run, found in the one before: regressed."""
        self.execute(3, {'A1': _Stub([_finding(self.A1)])})
        self.execute(2)
        run = self.execute(1, {'A1': _Stub([_finding(self.A1)])})
        self.assertEqual(self.mark(run, self.A1), 'regressed')
        self.assertEqual((run.new_count, run.fixed_count, run.regressed_count), (0, 0, 1))

    def test_skipped_check_lists_nothing_as_fixed(self):
        """SC-OPA-0030-3: a skipped check is no proof of a fix."""
        self.execute(2, {'A6': _Stub([_finding(self.A6)]), 'A1': _Stub([_finding(self.A1)])})
        run = self.execute(1, {'A6': _Stub(missing='Extension pg_stat_statements is not installed')})
        self.assertEqual(self.result(run, 'A6').state, 'skipped')
        self.assertFalse(run.finding_ids.filtered(lambda f: f.code == 'A6'))
        self.assertEqual(run.fixed_count, 1)
        self.assertEqual(self.mark(run, self.A1), 'fixed', 'a check that ran still lists its fixed finding')

    def test_failed_check_lists_nothing_as_fixed(self):
        """FR-OPA-0030: a check that failed in this run is no proof of a fix either."""
        self.execute(2, {'A6': _Stub([_finding(self.A6)]), 'A1': _Stub([_finding(self.A1)])})
        run = self.execute(1, {'A6': _Stub(error=RuntimeError('boom'))})
        self.assertEqual(self.result(run, 'A6').state, 'failed')
        self.assertFalse(run.finding_ids.filtered(lambda f: f.identity_key == self.A6))
        self.assertEqual(self.mark(run, self.A1), 'fixed', 'a check that ran still lists its fixed finding')

    def test_failed_run_is_not_the_previous_run(self):
        """FR-OPA-0030: the previous run is the previous completed run."""
        done = self.execute(3, {'A2': _Stub([_finding(self.A2)])})
        self.env['perf.audit.run'].sudo().create({
            'state': 'failed', 'start_date': self.now() - timedelta(hours=2),
            'end_date': self.now() - timedelta(hours=2), 'failure_reason': 'x'})
        run = self.execute(1, {'A2': _Stub([_finding(self.A2)])})
        self.assertEqual(run.previous_run_id, done)
        self.assertEqual(self.mark(run, self.A2), 'open')
