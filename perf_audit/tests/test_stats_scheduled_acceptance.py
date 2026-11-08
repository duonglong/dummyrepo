import time
from datetime import timedelta

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .acceptance_base import CHECK_CODES, HUGE, AuditAcceptanceCase


@tagged('post_install', '-at_install')
class TestStatsResetAcceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0004 (T-004)."""

    def test_recent_reset_is_weak_evidence(self):
        """FR-OPA-0004 / SC-OPA-0004-1: statistics reset just now: reset time shown, weak evidence."""
        self.env.cr.execute('SELECT pg_stat_reset()')
        run = self.start_run()
        self.assertTrue(run.stats_reset_date, 'the run shows the reset time')
        self.assertEqual(run.stats_reset_source, 'reset')
        self.assertLess(abs(run.start_date - run.stats_reset_date), timedelta(minutes=2))
        self.assertTrue(run.weak_evidence)

    def test_old_reset_is_not_weak_evidence(self):
        """FR-OPA-0004 / SC-OPA-0004-2: a reset older than the weak-evidence threshold is not weak.

        The threshold is set to one second (1/86400 day) and the reset made two seconds before.
        """
        self.env.cr.execute('SELECT pg_stat_reset()')
        time.sleep(2.5)
        self.set_threshold('weak_evidence', 1.0 / 86400)
        run = self.start_run()
        self.assertTrue(run.stats_reset_date)
        self.assertFalse(run.weak_evidence)

    def test_reset_within_threshold_is_weak_evidence(self):
        """FR-OPA-0004 boundary just inside: a reset younger than a 3650-day threshold is weak."""
        self.env.cr.execute('SELECT pg_stat_reset()')
        self.set_threshold('weak_evidence', 3650)
        run = self.start_run()
        self.assertTrue(run.weak_evidence)

    def test_run_keeps_the_weak_evidence_threshold_it_used(self):
        """FR-OPA-0004 / DESIGN: the run records the global thresholds it used."""
        self.set_threshold('weak_evidence', 3)
        run = self.start_run()
        self.assertTrue(run.thresholds_used)
        self.assertIn('3', str(run.thresholds_used))


@tagged('post_install', '-at_install')
class TestScheduledAuditAcceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0032, FR-OPA-0034 (T-004)."""

    def _cron(self):
        cron = self.env['ir.cron'].sudo().with_context(active_test=False).search(
            [('ir_actions_server_id.model_id.model', '=', 'perf.audit.run')])
        self.assertEqual(len(cron), 1, 'one scheduled audit must be shipped')
        return cron

    def test_scheduled_audit_is_off_after_install(self):
        """FR-OPA-0032 / SC-OPA-0032-2: the module ships the scheduled audit switched off."""
        self.assertFalse(self._cron().active)

    def test_scheduled_audit_starts_a_run_when_on(self):
        """FR-OPA-0032 / SC-OPA-0032-1: when its time comes the scheduled audit creates a full run."""
        cron = self._cron()
        cron.active = True
        Run = self.env['perf.audit.run'].sudo()
        before = Run.search([]).ids
        cron.ir_actions_server_id.sudo().run()
        self.env.flush_all()
        run = Run.search([('id', 'not in', before)])
        self.assertEqual(len(run), 1)
        self.assertEqual(run.trigger, 'scheduled')
        self.assertEqual(run.state, 'done')
        self.assertEqual(sorted(run.check_result_ids.mapped('check_id.code')), sorted(CHECK_CODES))

    def test_scheduled_start_is_skipped_while_a_run_runs(self):
        """FR-OPA-0032 / FR-OPA-0034: a scheduled start during a running run adds no second run."""
        cron = self._cron()
        cron.active = True
        Run = self.env['perf.audit.run'].sudo()
        running = Run.create({'state': 'running', 'start_date': self.now() - timedelta(minutes=2)})
        before = Run.search_count([])
        try:
            cron.ir_actions_server_id.sudo().run()
        except UserError:
            pass
        self.assertEqual(Run.search_count([]), before)
        self.assertEqual(running.state, 'running')
