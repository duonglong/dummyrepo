from datetime import datetime

from odoo import fields, sql_db
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, TransactionCase, tagged
from odoo.tests.common import freeze_time

LOCKED_MSG = "This profile is already running. Try again when it has finished."
NO_READ_MSG = "You are not allowed to read the records of this model."


class _Undo(Exception):
    """Raised to roll a savepoint back."""


@tagged('post_install', '-at_install')
class TestRunAcceptance(TransactionCase):
    """Acceptance tests of T-003 (run a profile: report, access-filtered engine, completeness).

    The profile is made through Odoo's Form helper (name, model, chosen fields);
    the Run button is the method ``action_run`` of the profile, called on the
    saved record as the analyst, because the button itself is built with the
    view by the task. The report is read back as the unsaved ``dpa.snapshot``
    of the profile.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env['res.groups'].search([('name', '=', 'Data Profiling')], limit=1)
        internal = cls.env.ref('base.group_user')
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.anna = Users.create({
            'name': 'Anna Analyst', 'login': 'anna_run', 'email': 'anna_run@example.com',
            'group_ids': [(6, 0, internal.ids + group.ids)],
        })
        cls.ben = Users.create({
            'name': 'Ben Member', 'login': 'ben_run', 'email': 'ben_run@example.com',
            'group_ids': [(6, 0, internal.ids + group.ids)],
        })
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.cron_model = cls.env['ir.model']._get('ir.cron')
        # Only the contacts a test creates are profiled: archive every other one.
        cls.env.cr.execute("UPDATE res_partner SET active = false")
        cls.env.invalidate_all()

    # -- helpers ---------------------------------------------------------
    def _contacts(self, vals_list):
        return self.env['res.partner'].create(
            [dict(vals, name=vals.get('name', 'Contact %d' % i)) for i, vals in enumerate(vals_list)])

    def _profile(self, field_names=(), name='Customer quality', user=None, **settings):
        """Create a profile of contacts through the form, as the analyst."""
        form = Form(self.env['dpa.profile'].with_user(user or self.anna))
        form.name = name
        form.model_id = self.partner_model
        for fname in field_names:
            form.field_ids.add(self.env['ir.model.fields']._get('res.partner', fname))
        for key, value in settings.items():
            setattr(form, key, value)
        return form.save()

    def _reports(self, profile):
        return self.env['dpa.snapshot'].search([('profile_id', '=', profile.id), ('is_saved', '=', False)])

    def _run(self, profile, user=None):
        profile.with_user(user or self.anna).action_run()
        self.env.flush_all()
        self.env.invalidate_all()
        report = self._reports(profile)
        self.assertEqual(len(report), 1, "one unsaved report must result from a run")
        return report

    def _line(self, report, field_name):
        line = report.field_line_ids.filtered(lambda l: l.field_name == field_name)
        self.assertEqual(len(line), 1, "one line expected for %s" % field_name)
        return line

    # -- FR-DPA-0002 -------------------------------------------------------
    def test_chosen_fields_only(self):
        """Requirements: FR-DPA-0002, SC-DPA-0002-1 (two chosen fields, two lines)."""
        self._contacts([{'email': 'a@x.com', 'phone': '555-0100'}])
        profile = self._profile(['email', 'phone'])
        report = self._run(profile)
        self.assertEqual(sorted(report.field_line_ids.mapped('field_name')), ['email', 'phone'])

    def test_no_field_chosen(self):
        """Requirements: FR-DPA-0002, BR-DPA-0002, SC-DPA-0002-2 (every profilable field)."""
        self._contacts([{'email': 'a@x.com'}])
        profile = self._profile()
        report = self._run(profile)
        names = set(report.field_line_ids.mapped('field_name'))
        self.assertTrue({'email', 'phone', 'country_id', 'user_id'} <= names)
        self.assertFalse(names & {'image_1920', 'create_date', 'id'})

    # -- FR-DPA-0003 -------------------------------------------------------
    def test_archived_records_left_out(self):
        """Requirements: FR-DPA-0003, BR-DPA-0006, SC-DPA-0003-1 (10 active, 2 archived)."""
        self._contacts([{} for _i in range(10)] + [{'active': False} for _i in range(2)])
        report = self._run(self._profile(['email']))
        self.assertEqual(report.record_count, 10)
        self.assertTrue(report.run_date)
        self.assertLess(abs(report.run_date - fields.Datetime.now()).total_seconds(), 300)

    def test_new_run_replaces_unsaved_report(self):
        """Requirements: FR-DPA-0003, SC-DPA-0003-2 (the 09:00 report is gone after the 10:00 run)."""
        self._contacts([{}])
        profile = self._profile(['email'])
        with freeze_time('2026-11-25 09:00:00'):
            first = self._run(profile)
            first_id = first.id
            self.assertEqual(first.run_date, datetime(2026, 11, 25, 9, 0, 0))
        with freeze_time('2026-11-25 10:00:00'):
            second = self._run(profile)
        self.assertEqual(second.run_date, datetime(2026, 11, 25, 10, 0, 0))
        self.assertFalse(self.env['dpa.snapshot'].browse(first_id).exists())

    # -- FR-DPA-0004 / BR-DPA-0001 ----------------------------------------
    def test_completeness_of_a_field(self):
        """Requirements: FR-DPA-0004, SC-DPA-0004-1 (8 of 10 with an email: 80.0%)."""
        self._contacts([{'email': 'p%d@x.com' % i} for i in range(8)] + [{}, {}])
        report = self._run(self._profile(['email']))
        line = self._line(report, 'email')
        self.assertEqual(line.record_count, 10)
        self.assertEqual(line.filled_count, 8)
        self.assertEqual(line.completeness, 80.0)

    def test_spaces_are_empty(self):
        """Requirements: FR-DPA-0004, BR-DPA-0001, SC-DPA-0004-2 (three spaces count as empty)."""
        self._contacts(
            [{'phone': '555-010%d' % i} for i in range(7)] + [{'phone': '   '}, {}, {}])
        report = self._run(self._profile(['phone']))
        line = self._line(report, 'phone')
        self.assertEqual(line.filled_count, 7)
        self.assertEqual(line.completeness, 70.0)

    def test_zero_and_unticked_are_values(self):
        """Requirements: FR-DPA-0004, BR-DPA-0001, SC-DPA-0004-3.

        Contacts stand in for products: the color index (1 of 4 is 0) is the
        price and the Employee tick box (none ticked) is Can be sold.
        """
        self._contacts([{'color': 0}, {'color': 3}, {'color': 4}, {'color': 5}])
        report = self._run(self._profile(['color', 'employee']))
        self.assertEqual(self._line(report, 'color').completeness, 100.0)
        self.assertEqual(self._line(report, 'employee').completeness, 100.0)

    # -- FR-DPA-0029 -------------------------------------------------------
    def _hold_profile_lock_elsewhere(self, profile):
        """Hold the run lock of the profile from another database session.

        The run lock is an advisory lock (ADR-DPA-004, key pair: app key, profile
        id). The app key is read from the lock a throw-away run takes, then
        rolled back; another real connection then takes the same lock.
        """
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

    def test_second_run_refused(self):
        """Requirements: FR-DPA-0029, NFR-REL-0001, SC-DPA-0029-1.

        Anna's run in progress is another session holding the profile's run
        lock; Ben's run is refused and the single report stays.
        """
        self._contacts([{}])
        profile = self._profile(['email'])
        existing = self.env['dpa.snapshot'].create({
            'name': 'Customer quality 2026-11-25', 'profile_id': profile.id,
            'model_name': 'res.partner', 'run_date': '2026-11-25 08:00:00',
        })
        self._hold_profile_lock_elsewhere(profile)
        with self.assertRaises(UserError) as cm:
            profile.with_user(self.ben).action_run()
        self.assertIn(LOCKED_MSG, str(cm.exception))
        self.env.invalidate_all()
        self.assertEqual(self._reports(profile), existing)

    # -- FR-DPA-0033 -------------------------------------------------------
    def test_run_refused_without_read_access(self):
        """Requirements: FR-DPA-0033, NFR-SEC-0002, SC-DPA-0033-1.

        Scheduled actions stand in for employees, which Anna may not read.
        """
        staff = self.env['dpa.profile'].create({'name': 'Staff data', 'model_id': self.cron_model.id})
        previous = self.env['dpa.snapshot'].create({
            'name': 'Staff data 2026-11-01', 'profile_id': staff.id,
            'model_name': 'ir.cron', 'run_date': '2026-11-01 08:00:00',
        })
        with self.assertRaises((UserError, AccessError)) as cm:
            staff.with_user(self.anna).action_run()
        self.assertIn(NO_READ_MSG, str(cm.exception))
        self.env.invalidate_all()
        self.assertEqual(self._reports(staff), previous)
        self.assertEqual(previous.run_date, datetime(2026, 11, 1, 8, 0, 0))
