from datetime import timedelta

from odoo.tests.common import tagged

from .acceptance_base import AuditAcceptanceCase


@tagged('post_install', '-at_install')
class TestB7Acceptance(AuditAcceptanceCase):
    """Requirements: FR-OPA-0022, FR-OPA-0023, FR-OPA-0024, FR-OPA-0025, BR-OPA-0005 (T-010)."""

    def _cron(self, name, model='res.partner', **vals):
        action = self.env['ir.actions.server'].create({
            'name': name, 'model_id': self.env['ir.model']._get(model).id,
            'state': 'code', 'code': 'pass'})
        base = {
            'ir_actions_server_id': action.id, 'user_id': self.env.ref('base.user_root').id,
            'interval_number': 1, 'interval_type': 'days',
            'nextcall': self.now() + timedelta(days=1), 'active': True,
        }
        base.update(vals)
        return self.env['ir.cron'].create(base)

    def _found(self, run, kind, cron):
        return self.findings(run, 'B7', kind=kind).filtered(lambda f: f.cron_id == cron)

    # FR-OPA-0022
    def test_failure_count_at_threshold(self):
        """FR-OPA-0022 / SC-OPA-0022-1: 4 failures in a row: finding with count 4 and first failure time."""
        first = self.now() - timedelta(hours=5)
        cron = self._cron('b7 failing', failure_count=4, first_failure_date=first)
        found = self._found(self.start_run(), 'failure', cron)
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'high')
        self.assertEqual(found.found_value, 4)
        self.assertEqual(found.threshold_value, 3)
        self.assertEqual(found.cron_id, cron)
        self.assertIn(str(first)[:16], self.dump(found))

    def test_failure_count_boundary(self):
        """FR-OPA-0022: a count equal to the threshold (3) is reported, 2 is not."""
        at = self._cron('b7 at threshold', failure_count=3, first_failure_date=self.now())
        below = self._cron('b7 below threshold', failure_count=2, first_failure_date=self.now())
        run = self.start_run()
        self.assertEqual(len(self._found(run, 'failure', at)), 1)
        self.assertFalse(self._found(run, 'failure', below))

    def test_no_failures_is_clean(self):
        """FR-OPA-0022 / SC-OPA-0022-2: failure count 0: no failure finding."""
        cron = self._cron('b7 healthy', failure_count=0)
        run = self.start_run()
        self.assertEqual(self.result(run, 'B7').state, 'done')
        self.assertFalse(self._found(run, 'failure', cron))

    # FR-OPA-0023
    def test_late_action(self):
        """FR-OPA-0023 / SC-OPA-0023-1: planned 3 hours ago and not run: late finding showing 3 hours."""
        cron = self._cron('b7 late', nextcall=self.now() - timedelta(hours=3, seconds=-30))
        found = self._found(self.start_run(), 'late', cron)
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'medium')
        self.assertAlmostEqual(found.found_value, 3, delta=0.1)
        self.assertEqual(found.threshold_value, 1)

    def test_lateness_boundary(self):
        """FR-OPA-0023: 59 minutes late is within the 1 hour threshold, 61 minutes is not."""
        within = self._cron('b7 59 min', nextcall=self.now() - timedelta(minutes=59))
        beyond = self._cron('b7 61 min', nextcall=self.now() - timedelta(minutes=61))
        run = self.start_run()
        self.assertFalse(self._found(run, 'late', within))
        self.assertEqual(len(self._found(run, 'late', beyond)), 1)

    def test_inactive_action_is_never_late(self):
        """FR-OPA-0023 / SC-OPA-0023-2: an inactive action planned 3 days ago gives no lateness finding."""
        cron = self._cron('b7 inactive', active=False, nextcall=self.now() - timedelta(days=3))
        self.assertFalse(self._found(self.start_run(), 'late', cron))

    # FR-OPA-0024
    def _progress(self, cron, minutes):
        progress = self.env['ir.cron.progress'].create({'cron_id': cron.id})
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE ir_cron_progress SET create_date = now() - make_interval(mins => %s), write_date = now() "
            "WHERE id = %s", [minutes, progress.id])
        self.env.invalidate_all()

    def test_run_longer_than_interval(self):
        """FR-OPA-0024 / SC-OPA-0024-1: every 5 minutes, latest run 12 minutes: finding 12 against 5."""
        cron = self._cron('b7 long', interval_number=5, interval_type='minutes')
        self._progress(cron, 12)
        found = self._found(self.start_run(), 'runtime', cron)
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'high')
        self.assertAlmostEqual(found.found_value, 12, delta=0.5)
        self.assertEqual(found.threshold_value, 5)

    def test_short_run_is_clean(self):
        """FR-OPA-0024 / SC-OPA-0024-2: every hour, latest run 2 minutes: no run-time finding."""
        cron = self._cron('b7 short', interval_number=1, interval_type='hours')
        self._progress(cron, 2)
        self.assertFalse(self._found(self.start_run(), 'runtime', cron))

    def test_run_equal_to_interval_is_clean(self):
        """FR-OPA-0024 boundary: a run that lasted exactly its interval is not longer than it."""
        cron = self._cron('b7 equal', interval_number=5, interval_type='minutes')
        self._progress(cron, 5)
        self.assertFalse(self._found(self.start_run(), 'runtime', cron))

    def test_action_without_progress_is_passed_over(self):
        """FR-OPA-0024: no progress record: no run-time finding and no failure of the check."""
        cron = self._cron('b7 no progress', interval_number=1, interval_type='minutes')
        run = self.start_run()
        self.assertFalse(self._found(run, 'runtime', cron))
        self.assertEqual(self.result(run, 'B7').state, 'done')

    # FR-OPA-0025
    def test_two_actions_on_one_model(self):
        """FR-OPA-0025 / SC-OPA-0025-1: two active actions on model x_order: one low finding naming both."""
        model = self.make_model('x_order')
        first = self._cron('b7 shared one', model='x_order', interval_number=10, interval_type='minutes')
        second = self._cron('b7 shared two', model='x_order', interval_number=2, interval_type='hours')
        run = self.start_run()
        found = self.findings(run, 'B7', kind='shared').filtered(lambda f: f.model_id == model)
        self.assertEqual(len(found), 1)
        self.assertEqual(found.severity, 'low')
        text = self.dump(found)
        for cron in (first, second):
            self.assertTrue(str(cron.id) in text or cron.cron_name.lower() in text)

    def test_one_action_on_a_model_is_clean(self):
        """FR-OPA-0025: a model targeted by a single active action gives no shared finding."""
        model = self.make_model('x_order')
        self._cron('b7 alone', model='x_order')
        run = self.start_run()
        self.assertFalse(self.findings(run, 'B7', kind='shared').filtered(lambda f: f.model_id == model))

    def test_inactive_second_action_does_not_count(self):
        """FR-OPA-0025 boundary: only active scheduled actions are counted."""
        model = self.make_model('x_order')
        self._cron('b7 active', model='x_order')
        self._cron('b7 off', model='x_order', active=False)
        run = self.start_run()
        self.assertFalse(self.findings(run, 'B7', kind='shared').filtered(lambda f: f.model_id == model))
