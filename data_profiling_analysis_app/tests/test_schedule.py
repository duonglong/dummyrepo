from datetime import datetime
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import ValidationError
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
        with self.assertRaises(ValidationError) as cm:
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

    def test_write_cannot_change_owner_or_companies(self):
        """Requirements: FR-DPA-0019 (the owner is set only by turning the schedule on)."""
        ben = self.anna.copy({'name': 'Ben', 'login': 'ben_own', 'email': 'ben_own@example.com'})
        companies = self.profile.schedule_company_ids
        self.profile.with_user(ben).write({
            'schedule_owner_id': ben.id, 'schedule_company_ids': [Command.clear()]})
        self.assertEqual(self.profile.schedule_owner_id, self.anna)
        self.assertEqual(self.profile.schedule_company_ids, companies)

    def test_create_cannot_set_the_owner(self):
        """Requirements: FR-DPA-0019."""
        ben = self.anna.copy({'name': 'Ben', 'login': 'ben_own', 'email': 'ben_own@example.com'})
        profile = self.env['dpa.profile'].with_user(self.anna).create({
            'name': 'Q', 'model_id': self.profile.model_id.id, 'schedule_active': True,
            'schedule_next_run': datetime(2026, 1, 31, 2, 0), 'schedule_owner_id': ben.id})
        self.assertEqual(profile.schedule_owner_id, self.anna)

    def test_copy_has_the_schedule_off(self):
        """Requirements: FR-DPA-0019 (a copy starts no schedule and has no owner)."""
        copy = self.profile.copy()
        self.assertFalse(copy.schedule_active)
        self.assertFalse(copy.schedule_owner_id)
        self.assertFalse(copy.schedule_next_run)

    def test_scheduled_run_keeps_the_unsaved_report(self):
        """Requirements: FR-DPA-0021 (a scheduled run leaves the shown report alone)."""
        self.profile.with_user(self.anna).action_run()
        shown = self.env['dpa.snapshot'].search([('profile_id', '=', self.profile.id), ('is_saved', '=', False)])
        self.assertEqual(len(shown), 1)
        self._check('2026-02-01 00:00:00')
        self.assertTrue(shown.exists())
        scheduled = self.env['dpa.snapshot'].search([('profile_id', '=', self.profile.id), ('origin', '=', 'scheduled')])
        self.assertEqual(len(scheduled), 1)
        self.assertTrue(scheduled.is_saved)

    def test_scheduled_run_counts_the_stored_companies_of_the_owner(self):
        """Requirements: FR-DPA-0021, NFR-SEC-0002 (stored companies the owner still has)."""
        other = self.env['res.company'].create({'name': 'Other'})
        self.anna.write({'company_ids': [Command.link(other.id)]})
        self.profile.sudo().write({'schedule_active': False})
        self.profile.with_user(self.anna).with_context(allowed_company_ids=[other.id]).write({
            'schedule_active': True, 'schedule_next_run': datetime(2026, 1, 31, 2, 0)})
        self.assertEqual(self.profile.schedule_company_ids, other)
        self._check('2026-02-01 00:00:00')
        snapshot = self.env['dpa.snapshot'].search([('profile_id', '=', self.profile.id), ('origin', '=', 'scheduled')])
        self.assertEqual(snapshot.company_ids, other)

    def test_scheduled_run_falls_back_to_the_default_company(self):
        """Requirements: FR-DPA-0021 (no stored company left to the owner: the owner's default company)."""
        other = self.env['res.company'].create({'name': 'Gone'})
        self.anna.write({'company_ids': [Command.link(other.id)]})
        self.profile.sudo().write({'schedule_active': False})
        self.profile.with_user(self.anna).with_context(allowed_company_ids=[other.id]).write({
            'schedule_active': True, 'schedule_next_run': datetime(2026, 1, 31, 2, 0)})
        self.anna.write({'company_ids': [Command.unlink(other.id)]})
        self._check('2026-02-01 00:00:00')
        snapshot = self.env['dpa.snapshot'].search([('profile_id', '=', self.profile.id), ('origin', '=', 'scheduled')])
        self.assertEqual(snapshot.company_ids, self.anna.company_id)
