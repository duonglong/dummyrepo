from datetime import timedelta

from odoo import fields
from odoo.tests.common import tagged

from .common import PerfAuditCase


@tagged('post_install', '-at_install')
class TestScheduled(PerfAuditCase):
    """Requirements: FR-OPA-0032, FR-OPA-0034."""

    def _cron(self):
        return self.env.ref('perf_audit.ir_cron_scheduled_audit').sudo()

    def test_cron_inactive_after_install(self):
        """SC-OPA-0032-2: the scheduled audit ships switched off."""
        self.assertFalse(self._cron().active)

    def test_cron_creates_scheduled_run(self):
        """SC-OPA-0032-1: the cron method creates a scheduled run for the scheduler user."""
        cron = self._cron()
        before = self.env['perf.audit.run'].search([]).ids
        self.env['perf.audit.run'].with_user(cron.user_id).sudo().action_start_scheduled_audit()
        run = self.env['perf.audit.run'].search([('id', 'not in', before)])
        self.assertEqual(run.trigger, 'scheduled')
        self.assertEqual(run.state, 'done')
        self.assertEqual(run.user_id, self.env.ref('base.user_root'))

    def test_scheduled_start_skipped_while_running(self):
        """FR-OPA-0034: a running run makes the scheduled start a silent no-op."""
        Run = self.env['perf.audit.run']
        Run.create({'state': 'running', 'start_date': fields.Datetime.now() - timedelta(minutes=5)})
        count = Run.search_count([])
        self.assertIsNone(Run.action_start_scheduled_audit())
        self.assertEqual(Run.search_count([]), count)
