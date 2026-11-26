from datetime import datetime

from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, tagged
from odoo.tests.common import freeze_time

HAS_SNAPSHOTS_MSG = "This profile has snapshots. Archive it, or delete its snapshots first."


@tagged('post_install', '-at_install')
class TestSnapshotAcceptance(TransactionCase):
    """Acceptance tests of T-007 (save a report as a snapshot, delete snapshots, keep profiles with history).

    The profile is made through Odoo's Form helper and run with ``action_run``
    as the analyst. The Save as snapshot button of the report is the method
    ``action_save_snapshot`` of ``dpa.snapshot`` (a name the design leaves
    open); it is called on the saved report as the analyst, and the button's
    presence is read from the form view. The confirmation dialog of the delete
    is a view matter, so the delete itself (``unlink``) is tested.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env['res.groups'].search([('name', '=', 'Data Profiling')], limit=1)
        internal = cls.env.ref('base.group_user')
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.anna = Users.create({
            'name': 'Anna Analyst', 'login': 'anna_snap', 'email': 'anna_snap@example.com',
            'group_ids': [(6, 0, internal.ids + group.ids)],
        })
        cls.ben = Users.create({
            'name': 'Ben Member', 'login': 'ben_snap', 'email': 'ben_snap@example.com',
            'group_ids': [(6, 0, internal.ids + group.ids)],
        })
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        # Only the contacts a test creates are profiled: archive every other one.
        cls.env.cr.execute("UPDATE res_partner SET active = false")
        cls.env.invalidate_all()

    # -- helpers ---------------------------------------------------------
    def _contacts(self, vals_list):
        return self.env['res.partner'].create(
            [dict(vals, name=vals.get('name', 'Contact %d' % i)) for i, vals in enumerate(vals_list)])

    def _profile(self, field_names=('email',), name='Customer quality'):
        form = Form(self.env['dpa.profile'].with_user(self.anna))
        form.name = name
        form.model_id = self.partner_model
        for fname in field_names:
            form.field_ids.add(self.env['ir.model.fields']._get('res.partner', fname))
        return form.save()

    def _run(self, profile, when, user=None):
        with freeze_time(when):
            profile.with_user(user or self.anna).action_run()
            self.env.flush_all()
        self.env.invalidate_all()
        report = self.env['dpa.snapshot'].search([('profile_id', '=', profile.id), ('is_saved', '=', False)])
        self.assertEqual(len(report), 1, "one unsaved report must result from a run")
        return report

    def _save(self, report, user=None):
        report.with_user(user or self.anna).action_save_snapshot()
        self.env.flush_all()
        self.env.invalidate_all()
        return report

    def _saved(self, profile):
        return self.env['dpa.snapshot'].search([('profile_id', '=', profile.id), ('is_saved', '=', True)])

    def _line(self, snapshot, field_name):
        return snapshot.field_line_ids.filtered(lambda l: l.field_name == field_name)

    # -- FR-DPA-0018 -------------------------------------------------------
    def test_snapshot_kept_unchanged(self):
        """Requirements: FR-DPA-0018, SC-DPA-0018-1.

        Anna saves the 09:00 report (Email completeness 80.0%, threshold 20%),
        then sets the threshold to 10% and runs again: the snapshot is unchanged.
        """
        self._contacts([{'email': 'a%d@example.com' % i} for i in range(4)] + [{}])
        profile = self._profile()
        report = self._run(profile, '2026-11-25 09:00:00')
        self.assertEqual(self._line(report, 'email').completeness, 80.0)
        self._save(report)
        profile.with_user(self.anna).write({'empty_threshold': 10.0})
        self._run(profile, '2026-11-25 11:00:00')
        self.env.invalidate_all()
        snapshots = self._saved(profile)
        self.assertEqual(snapshots, report)
        self.assertEqual(self._line(snapshots, 'email').completeness, 80.0)
        self.assertEqual(snapshots.empty_threshold, 20.0)
        self.assertEqual(snapshots.origin, 'manual')
        self.assertEqual(snapshots.user_id, self.anna)
        self.assertEqual(snapshots.run_date, datetime(2026, 11, 25, 9, 0, 0))

    def test_saved_snapshot_refuses_every_change(self):
        """Requirements: FR-DPA-0018 (never change it afterwards)."""
        self._contacts([{'email': 'a@example.com'}])
        profile = self._profile()
        snapshot = self._save(self._run(profile, '2026-11-25 09:00:00'))
        self.assertTrue(snapshot.is_saved)
        with self.assertRaises(UserError):
            snapshot.with_user(self.anna).write({'record_count': 99})
        with self.assertRaises(UserError):
            self._line(snapshot, 'email').with_user(self.anna).write({'completeness': 1.0})
        self.env.invalidate_all()
        self.assertEqual(snapshot.record_count, 1)
        self.assertEqual(self._line(snapshot, 'email').completeness, 100.0)

    def test_save_keeps_the_report_lines(self):
        """Requirements: FR-DPA-0018 (the snapshot is the report, saved)."""
        self._contacts([{'email': 'a@example.com'}, {}])
        profile = self._profile()
        report = self._run(profile, '2026-11-25 09:00:00')
        lines_before = report.field_line_ids.ids
        self._save(report)
        self.assertEqual(report.field_line_ids.ids, lines_before)
        self.assertEqual(profile.snapshot_count, 1)

    # -- SC-DPA-0018-2 -------------------------------------------------------
    def test_save_button_gone_once_saved(self):
        """Requirements: FR-DPA-0018, SC-DPA-0018-2.

        The button is read from the form view arch: it must be hidden by a
        condition on ``is_saved``. Saving the same report again adds no second
        snapshot (refused or ignored).
        """
        self._contacts([{'email': 'a@example.com'}])
        profile = self._profile()
        report = self._run(profile, '2026-11-25 09:00:00')
        arch = self.env['dpa.snapshot'].with_user(self.anna).get_view(view_type='form')['arch']
        buttons = etree.fromstring(arch).xpath("//button[@name='action_save_snapshot']")
        self.assertEqual(len(buttons), 1, "the form carries the Save as snapshot button")
        self.assertIn('is_saved', buttons[0].get('invisible') or '')
        self._save(report)
        try:
            self._save(report)
        except UserError:
            pass
        self.env.invalidate_all()
        self.assertEqual(len(self._saved(profile)), 1)
        self.assertEqual(profile.snapshot_count, 1)

    # -- FR-DPA-0030 -------------------------------------------------------
    def test_snapshot_deleted(self):
        """Requirements: FR-DPA-0030, SC-DPA-0030-1.

        Another member of the group may delete it: the group shares snapshots.
        """
        self._contacts([{'email': 'a@example.com'}])
        profile = self._profile()
        november = self._save(self._run(profile, '2026-11-01 09:00:00'))
        december = self._save(self._run(profile, '2026-12-01 09:00:00'))
        line_ids = november.field_line_ids.ids
        self.assertTrue(line_ids)
        november.with_user(self.ben).unlink()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(profile.snapshot_ids, december)
        self.assertFalse(self.env['dpa.snapshot.field'].search([('id', 'in', line_ids)]))
        self.assertEqual(self.env['dpa.snapshot'].search([('profile_id', '=', profile.id)]), december)

    # -- FR-DPA-0031 -------------------------------------------------------
    def test_profile_with_snapshots_kept(self):
        """Requirements: FR-DPA-0031, SC-DPA-0031-1."""
        self._contacts([{'email': 'a@example.com'}])
        profile = self._profile()
        first = self._save(self._run(profile, '2026-11-01 09:00:00'))
        second = self._save(self._run(profile, '2026-12-01 09:00:00'))
        with self.assertRaises(UserError) as cm:
            profile.with_user(self.anna).unlink()
        self.assertIn(HAS_SNAPSHOTS_MSG, str(cm.exception))
        self.env.invalidate_all()
        self.assertTrue(profile.exists())
        self.assertEqual(profile.snapshot_ids, first | second)
        self.assertTrue(first.exists() and second.exists())

    def test_profile_without_snapshots_deleted(self):
        """Requirements: FR-DPA-0031 (boundary: once its snapshots are deleted the profile may go)."""
        self._contacts([{'email': 'a@example.com'}])
        profile = self._profile()
        snapshot = self._save(self._run(profile, '2026-11-01 09:00:00'))
        with self.assertRaises(UserError):
            profile.with_user(self.anna).unlink()
        snapshot.with_user(self.anna).unlink()
        profile.with_user(self.anna).unlink()
        self.assertFalse(profile.exists())
