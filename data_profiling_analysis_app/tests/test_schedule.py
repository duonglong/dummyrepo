from datetime import datetime
from unittest.mock import patch

from odoo import Command
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import freeze_time


@tagged('post_install', '-at_install')
class TestScheduleOwn(TransactionCase):
    """Own tests of T-008: what the locked acceptance tests leave open."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env.ref('data_profiling_analysis_app.group_data_profiling')
        cls.anna = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Anna', 'login': 'anna_own', 'email': 'anna_own@example.com',
            'group_ids': [Command.set((cls.env.ref('base.group_user') | group).ids)],
        })
        model = cls.env['ir.model']._get('res.partner')
        cls.profile = cls.env['dpa.profile'].with_user(cls.anna).create({
            'name': 'P', 'model_id': model.id, 'schedule_active': True,
            'schedule_interval_number': 1, 'schedule_interval_type': 'months',
            'schedule_next_run': datetime(2026, 1, 31, 2, 0),
        })

    def _check(self, when):
        with freeze_time(when):
            self.env['dpa.profile']._cron_take_snapshots()
        self.env.invalidate_all()

    def test_turning_on_stores_owner_and_companies(self):
        """Requirements: FR-DPA-0019."""
        self.assertEqual(self.profile.schedule_owner_id, self.anna)
        self.assertEqual(self.profile.schedule_company_ids, self.anna.company_ids[:1])

    def test_missing_next_run_refused(self):
        """Requirements: FR-DPA-0019."""
        with self.assertRaises(Exception) as cm:
            self.profile.write({'schedule_next_run': False})
        self.assertIn("Set the next run date of the schedule.", str(cm.exception))

    def test_monthly_next_run_keeps_the_day(self):
        """Requirements: FR-DPA-0021 (whole intervals from the first date, never later than now)."""
        self._check('2026-03-15 00:00:00')
        self.assertEqual(self.profile.schedule_next_run, datetime(2026, 3, 31, 2, 0))

    def test_success_clears_an_earlier_failure(self):
        """Requirements: NFR-REL-0001."""
        with patch('odoo.addons.data_profiling_analysis_app.services.profiler.profile', side_effect=ValueError("x")):
            self._check('2026-02-01 00:00:00')
        self.assertTrue(self.profile.schedule_notice.startswith("The scheduled run of "))
        self._check('2026-02-01 01:00:00')
        self.assertFalse(self.profile.schedule_failure_date)
        self.assertFalse(self.profile.schedule_notice)

    def test_archiving_stops_the_schedule_whatever_the_path(self):
        """Requirements: FR-DPA-0031."""
        self.profile.write({'active': False, 'schedule_active': True})
        self.assertFalse(self.profile.schedule_active)
