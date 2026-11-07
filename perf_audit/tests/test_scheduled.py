from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.modules import module as odoo_module
from odoo.tests.common import tagged

from .common import PerfAuditCase


@tagged('post_install', '-at_install')
class TestScheduled(PerfAuditCase):
    """Requirements: FR-OPA-0032, FR-OPA-0034."""

    def _cron(self):
        return self.env.ref('perf_audit.ir_cron_scheduled_audit').sudo()  # the cron belongs to the superuser

    def test_cron_inactive_after_install(self):
        """SC-OPA-0032-2: the scheduled audit ships switched off."""
        self.assertFalse(self._cron().active)

    def test_cron_creates_scheduled_run(self):
        """SC-OPA-0032-1: the cron method creates a scheduled run for the scheduler user."""
        cron = self._cron()
        before = self.env['perf.audit.run'].search([]).ids
        self.env['perf.audit.run'].with_user(cron.user_id).sudo()._cron_start_scheduled_audit()  # as the scheduler runs it
        run = self.env['perf.audit.run'].search([('id', 'not in', before)])
        self.assertEqual(run.trigger, 'scheduled')
        self.assertEqual(run.state, 'done')
        self.assertEqual(run.user_id, self.env.ref('base.user_root'))

    def test_scheduled_start_skipped_while_running(self):
        """FR-OPA-0034: a running run makes the scheduled start a silent no-op."""
        Run = self.env['perf.audit.run']
        Run.create({'state': 'running', 'start_date': fields.Datetime.now() - timedelta(minutes=5)})
        count = Run.search_count([])
        self.assertIsNone(Run._cron_start_scheduled_audit())
        self.assertEqual(Run.search_count([]), count)

    def test_cron_code_names_the_private_entry(self):
        """The scheduled audit calls a method that cannot be reached remotely."""
        self.assertEqual(self._cron().code.strip(), 'model._cron_start_scheduled_audit()')
        self.assertFalse(hasattr(self.env['perf.audit.run'], 'action_start_scheduled_audit'))

    def test_cron_planned_at_two_in_the_morning(self):
        """SC-OPA-0032-1: the scheduled audit ships planned at 02:00, a quiet hour."""
        nextcall = self._cron().nextcall
        self.assertEqual((nextcall.hour, nextcall.minute), (2, 0))

    def test_scheduled_run_is_left_to_the_background_action(self):
        """FR-OPA-0032 / FR-OPA-0034: outside tests a scheduled start ends with the run running.

        The scheduled action's transaction then commits the running run, so a start by hand sees it,
        and the background action executes it like a run started by hand.
        """
        Run = self.env['perf.audit.run']
        executor = self.env.ref('perf_audit.ir_cron_execute_audit')
        Trigger = self.env['ir.cron.trigger'].sudo()  # triggers are technical records
        triggers = Trigger.search_count([('cron_id', '=', executor.id)])
        before = Run.search([]).ids
        with patch.object(odoo_module, 'current_test', False):
            Run.with_user(self._cron().user_id)._cron_start_scheduled_audit()
        run = Run.search([('id', 'not in', before)])
        self.assertEqual((run.state, run.trigger), ('running', 'scheduled'))
        self.assertFalse(run.check_result_ids)
        self.assertEqual(Trigger.search_count([('cron_id', '=', executor.id)]), triggers + 1)
        Run._execute_pending_runs()
        self.assertEqual(run.state, 'done')

    def test_scheduled_run_failing_is_kept_as_failed(self):
        """FR-OPA-0032: an unexpected error of a scheduled run closes it as failed with its reason."""
        Run = self.env['perf.audit.run']
        before = Run.search([]).ids
        with patch.object(odoo_module, 'current_test', False):
            Run._cron_start_scheduled_audit()
        run = Run.search([('id', 'not in', before)])
        with patch.object(type(Run), '_execute', side_effect=RuntimeError('disk full')):
            Run._execute_pending_runs()
        self.assertEqual((run.state, run.failure_reason), ('failed', 'disk full'))
