from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import PerfAuditCase


@tagged('post_install', '-at_install')
class TestOneRun(PerfAuditCase):
    """Requirements: FR-OPA-0034, FR-OPA-0001."""

    def _running(self, **ago):
        return self.env['perf.audit.run'].create(
            {'state': 'running', 'start_date': fields.Datetime.now() - timedelta(**ago)})

    def _start(self):
        return self.env['perf.audit.run'].with_user(self.audit_admin).action_start_audit()

    def test_fresh_running_run_blocks_a_new_one(self):
        """SC-OPA-0034-1: a run of 2 minutes ago refuses a new start and is named."""
        running = self._running(minutes=2)
        with self.assertRaises(UserError) as caught:
            self._start()
        self.assertIn(running.name, str(caught.exception))

    def test_stale_run_is_closed_and_new_one_starts(self):
        """SC-OPA-0034-2: a run of 3 hours ago is closed as failed and a new run is done."""
        old = self._running(hours=3)
        action = self._start()
        self.assertEqual(old.state, 'failed')
        self.assertTrue(old.failure_reason)
        self.assertTrue(old.end_date)
        new = self.env['perf.audit.run'].browse(action['res_id'])
        self.assertEqual((new.state, new.user_id, new.trigger), ('done', self.audit_admin, 'manual'))

    def test_boundary_around_one_hour(self):
        """SC-OPA-0034-1/2 boundary: 59 minutes still blocks, 61 minutes is stale."""
        self.assertEqual(self._running(minutes=59).state, 'running')
        with self.assertRaises(UserError):
            self._start()
        self.env['perf.audit.run'].search([('state', '=', 'running')]).unlink()
        self._running(minutes=61)
        self._start()

    def test_pending_runs_are_executed_by_the_background_action(self):
        """FR-OPA-0001: a started run not yet executed is executed once by the background action."""
        run = self.env['perf.audit.run'].create({'user_id': self.audit_admin.id})
        self.env['perf.audit.run']._execute_pending_runs()
        self.assertEqual(run.state, 'done')
        self.assertEqual(len(run.check_result_ids), 15)
