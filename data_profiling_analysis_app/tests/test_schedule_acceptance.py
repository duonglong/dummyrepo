from datetime import datetime
from unittest.mock import patch

from odoo import sql_db
from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, tagged
from odoo.tests.common import freeze_time

INTERVAL_MSG = "The schedule interval must be at least 1."


class _Undo(Exception):
    """Raised to roll a savepoint back."""


@tagged('post_install', '-at_install')
class TestScheduleAcceptance(TransactionCase):
    """Acceptance tests of T-008 (snapshot schedule and the hourly scheduled action).

    The schedule is set through Odoo's Form helper on the profile (schedule on,
    interval number and type, next run). The hourly action is the method
    ``_cron_take_snapshots`` of ``dpa.profile`` (a name the design leaves
    open), called as the superuser like the cron does, inside ``freeze_time``;
    a scheduled ``ir.cron`` on that model that runs hourly is checked too.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.group = cls.env['res.groups'].search([('name', '=', 'Data Profiling')], limit=1)
        internal = cls.env.ref('base.group_user')
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.anna = Users.create({
            'name': 'Anna Analyst', 'login': 'anna_sch', 'email': 'anna_sch@example.com',
            'group_ids': [(6, 0, internal.ids + cls.group.ids)],
        })
        cls.ben = Users.create({
            'name': 'Ben Member', 'login': 'ben_sch', 'email': 'ben_sch@example.com',
            'group_ids': [(6, 0, internal.ids + cls.group.ids)],
        })
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.env.cr.execute("UPDATE res_partner SET active = false")
        cls.env.invalidate_all()

    # -- helpers ---------------------------------------------------------
    def _profile(self, number=1, kind='weeks', next_run=datetime(2026, 12, 1, 2, 0), name='Customer quality',
                 schedule=True):
        self.env['res.partner'].create([{'name': 'Contact %d' % i, 'email': 'c%d@example.com' % i} for i in range(3)])
        form = Form(self.env['dpa.profile'].with_user(self.anna))
        form.name = name
        form.model_id = self.partner_model
        form.field_ids.add(self.env['ir.model.fields']._get('res.partner', 'email'))
        if schedule:
            form.schedule_active = True
            form.schedule_interval_number = number
            form.schedule_interval_type = kind
            form.schedule_next_run = next_run
        return form.save()

    def _check(self, when):
        with freeze_time(when):
            self.env['dpa.profile']._cron_take_snapshots()
            self.env.flush_all()
        self.env.invalidate_all()

    def _snapshots(self, profile):
        return self.env['dpa.snapshot'].search([('profile_id', '=', profile.id)])

    def _hold_profile_lock_elsewhere(self, profile):
        """Hold the run lock of the profile from another database session (as a run in progress)."""
        probe = self.env['dpa.profile'].create({'name': 'Probe', 'model_id': self.partner_model.id})
        key = None
        try:
            with self.env.cr.savepoint():
                probe.with_user(self.anna).action_run()
                self.env.cr.execute("SELECT pg_backend_pid()")
                pid = self.env.cr.fetchone()[0]
                other = sql_db.db_connect(self.env.cr.dbname).cursor()
                try:
                    other.execute(
                        "SELECT classid::bigint FROM pg_locks WHERE locktype = 'advisory' "
                        "AND pid = %s AND objsubid = 2 AND objid = %s",
                        (pid, probe.id))
                    rows = other.fetchall()
                finally:
                    other.close()
                self.assertTrue(rows, "a run must take an advisory lock keyed by the profile id")
                key = rows[0][0]
                raise _Undo()
        except _Undo:
            pass
        self.env.invalidate_all()
        key = key - 2 ** 32 if key >= 2 ** 31 else key
        holder = sql_db.db_connect(self.env.cr.dbname).cursor()

        def release():
            holder.execute("SELECT pg_advisory_unlock_all()")
            holder.close()

        self.addCleanup(release)
        holder.execute("SELECT pg_try_advisory_lock(%s, %s)", (key, profile.id))
        self.assertTrue(holder.fetchone()[0])

    # -- FR-DPA-0019 -------------------------------------------------------
    def test_weekly_schedule(self):
        """Requirements: FR-DPA-0019, SC-DPA-0019-1."""
        profile = self._profile()
        self.assertTrue(profile.schedule_active)
        self.assertEqual(profile.schedule_interval_number, 1)
        self.assertEqual(profile.schedule_interval_type, 'weeks')
        self.assertEqual(profile.schedule_next_run, datetime(2026, 12, 1, 2, 0))
        self.assertEqual(profile.schedule_owner_id, self.anna)

    def test_schedule_off_keeps_snapshots(self):
        """Requirements: FR-DPA-0019 (turning the schedule off keeps the snapshots taken)."""
        profile = self._profile()
        self._check('2026-12-01 02:30:00')
        self.assertEqual(len(self._snapshots(profile)), 1)
        profile.with_user(self.anna).write({'schedule_active': False})
        self.env.invalidate_all()
        self.assertFalse(profile.schedule_active)
        self.assertEqual(len(self._snapshots(profile)), 1)

    def test_hourly_scheduled_action_exists(self):
        """Requirements: FR-DPA-0021 (the schedule is checked every hour)."""
        crons = self.env['ir.cron'].with_context(active_test=False).search([('model_id.model', '=', 'dpa.profile')])
        self.assertTrue(crons, "a scheduled action on dpa.profile is expected")
        cron = crons[0]
        self.assertEqual((cron.interval_number, cron.interval_type), (1, 'hours'))
        self.assertTrue(cron.active)

    # -- FR-DPA-0020 -------------------------------------------------------
    def test_zero_interval_refused(self):
        """Requirements: FR-DPA-0020, SC-DPA-0020-1."""
        profile = self._profile()
        with self.assertRaises(UserError) as cm:
            form = Form(profile.with_user(self.anna))
            form.schedule_interval_number = 0
            form.save()
        self.assertIn(INTERVAL_MSG, str(cm.exception))
        self.env.invalidate_all()
        self.assertEqual(profile.schedule_interval_number, 1)
        self.assertEqual(profile.schedule_interval_type, 'weeks')

    def test_negative_interval_refused_and_one_accepted(self):
        """Requirements: FR-DPA-0020 (boundary: -1 refused, 1 accepted)."""
        profile = self._profile(number=2, kind='days')
        with self.assertRaises(UserError) as cm:
            profile.with_user(self.anna).write({'schedule_interval_number': -1})
        self.assertIn(INTERVAL_MSG, str(cm.exception))
        self.env.invalidate_all()
        self.assertEqual(profile.schedule_interval_number, 2)
        profile.with_user(self.anna).write({'schedule_interval_number': 1})
        self.assertEqual(profile.schedule_interval_number, 1)

    # -- FR-DPA-0021 -------------------------------------------------------
    def test_weekly_snapshot(self):
        """Requirements: FR-DPA-0021, SC-DPA-0021-1."""
        profile = self._profile()
        self._check('2026-12-01 02:30:00')
        snapshot = self._snapshots(profile)
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(snapshot.origin, 'scheduled')
        self.assertTrue(snapshot.is_saved)
        self.assertEqual(snapshot.user_id, self.anna)
        self.assertEqual(snapshot.run_date.date().isoformat(), '2026-12-01')
        self.assertEqual(snapshot.record_count, 3)
        self.assertEqual(profile.schedule_next_run, datetime(2026, 12, 8, 2, 0))

    def test_missed_dates_give_one_snapshot(self):
        """Requirements: FR-DPA-0021, SC-DPA-0021-2."""
        profile = self._profile()
        self._check('2026-12-20 10:00:00')
        self.assertEqual(len(self._snapshots(profile)), 1)
        self.assertEqual(profile.schedule_next_run, datetime(2026, 12, 22, 2, 0))

    def test_not_due_one_second_before(self):
        """Requirements: FR-DPA-0021 (boundary: just before the next run nothing happens)."""
        profile = self._profile()
        self._check('2026-12-01 01:59:59')
        self.assertFalse(self._snapshots(profile))
        self.assertEqual(profile.schedule_next_run, datetime(2026, 12, 1, 2, 0))

    def test_due_exactly_at_the_next_run(self):
        """Requirements: FR-DPA-0021 (boundary: at the next run date it is due)."""
        profile = self._profile()
        self._check('2026-12-01 02:00:00')
        self.assertEqual(len(self._snapshots(profile)), 1)
        self.assertEqual(profile.schedule_next_run, datetime(2026, 12, 8, 2, 0))

    def test_next_run_is_after_now_when_now_is_a_date(self):
        """Requirements: FR-DPA-0021 (the first date after now: now itself is not after now)."""
        profile = self._profile()
        self._check('2026-12-08 02:00:00')
        self.assertEqual(len(self._snapshots(profile)), 1)
        self.assertEqual(profile.schedule_next_run, datetime(2026, 12, 15, 2, 0))

    def test_days_and_months_intervals(self):
        """Requirements: FR-DPA-0021 (whole intervals of days and of months)."""
        by_days = self._profile(number=3, kind='days', name='Every 3 days')
        by_months = self._profile(number=1, kind='months', name='Monthly')
        self._check('2026-12-01 02:30:00')
        self.assertEqual(by_days.schedule_next_run, datetime(2026, 12, 4, 2, 0))
        self.assertEqual(by_months.schedule_next_run, datetime(2027, 1, 1, 2, 0))
        self.assertEqual(len(self._snapshots(by_days)), 1)
        self.assertEqual(len(self._snapshots(by_months)), 1)

    def test_unscheduled_profile_is_left_alone(self):
        """Requirements: FR-DPA-0021 (a profile whose schedule is off takes no snapshot)."""
        profile = self._profile(schedule=False)
        self._check('2026-12-01 02:30:00')
        self.assertFalse(self._snapshots(profile))

    # -- FR-DPA-0022 -------------------------------------------------------
    def test_owner_left_the_group(self):
        """Requirements: FR-DPA-0022, SC-DPA-0022-1."""
        profile = self._profile()
        with freeze_time('2026-11-28 10:00:00'):
            self.anna.write({'group_ids': [(3, self.group.id)]})
        self._check('2026-12-01 02:30:00')
        self.assertFalse(self._snapshots(profile))
        self.assertFalse(profile.schedule_active)
        self.assertEqual(profile.schedule_stop_date, datetime(2026, 12, 1, 2, 30, 0))
        notice = profile.schedule_notice
        self.assertTrue(notice.startswith("The schedule was stopped on "), notice)
        self.assertTrue(notice.endswith(" because its owner can no longer run profiling."), notice)
        self.assertTrue(
            any(shape in notice for shape in ('1 December 2026', '12/01/2026', '2026-12-01', 'Dec 1, 2026')), notice)

    def test_owner_archived(self):
        """Requirements: FR-DPA-0022 (an archived owner stops the schedule as well)."""
        profile = self._profile()
        self.anna.write({'active': False})
        self._check('2026-12-01 02:30:00')
        self.assertFalse(self._snapshots(profile))
        self.assertFalse(profile.schedule_active)
        self.assertTrue(profile.schedule_stop_date)

    def test_another_analyst_takes_the_schedule_over(self):
        """Requirements: FR-DPA-0022, FR-DPA-0019 (turned on again, the other analyst becomes the owner)."""
        profile = self._profile()
        self.anna.write({'group_ids': [(3, self.group.id)]})
        self._check('2026-12-01 02:30:00')
        form = Form(profile.with_user(self.ben))
        form.schedule_active = True
        form.schedule_next_run = datetime(2026, 12, 8, 2, 0)
        form.save()
        self.assertEqual(profile.schedule_owner_id, self.ben)

    # -- FR-DPA-0029 -------------------------------------------------------
    def test_scheduled_run_waits(self):
        """Requirements: FR-DPA-0029, NFR-REL-0001, SC-DPA-0029-2.

        Anna's run in progress is another session holding the profile's run lock.
        """
        profile = self._profile()
        self._hold_profile_lock_elsewhere(profile)
        self._check('2026-12-01 02:30:00')
        self.assertFalse(self._snapshots(profile))
        self.assertTrue(profile.schedule_active)
        self.assertEqual(profile.schedule_next_run, datetime(2026, 12, 1, 2, 0))

    # -- NFR-REL-0001 --------------------------------------------------------
    def test_failed_scheduled_run_keeps_the_next_run(self):
        """Requirements: NFR-REL-0001 (no snapshot, next run kept, failure shown on the profile)."""
        profile = self._profile()
        with patch(
                'odoo.addons.data_profiling_analysis_app.services.profiler.profile',
                side_effect=ValueError("boom")):
            self._check('2026-12-01 02:30:00')
        self.assertFalse(self._snapshots(profile))
        self.assertEqual(profile.schedule_next_run, datetime(2026, 12, 1, 2, 0))
        self.assertTrue(profile.schedule_active)
        self.assertEqual(profile.schedule_failure_date, datetime(2026, 12, 1, 2, 30, 0))
        self.assertTrue(profile.schedule_failure_message)

    # -- FR-DPA-0031 (archive part) -----------------------------------------
    def test_archived_profile_stops_its_schedule(self):
        """Requirements: FR-DPA-0031, SC-DPA-0031-2 (the comparison part is tested with T-009)."""
        profile = self._profile()
        self._check('2026-12-01 02:30:00')
        profile.with_user(self.anna).action_archive()
        self.env.invalidate_all()
        self.assertFalse(profile.active)
        self.assertFalse(profile.schedule_active)
        self.assertEqual(len(self._snapshots(profile)), 1)
