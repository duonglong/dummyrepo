from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSnapshotFrozen(TransactionCase):
    """Own tests of T-007: the lines and values of a saved snapshot never change."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env['res.groups'].search([('name', '=', 'Data Profiling')], limit=1)
        internal = cls.env.ref('base.group_user')
        cls.anna, cls.ben = (
            cls.env['res.users'].with_context(no_reset_password=True).create({
                'name': name, 'login': name.lower(), 'email': '%s@example.com' % name.lower(),
                'group_ids': [Command.set((internal | group).ids)],
            }) for name in ('Anna', 'Ben')
        )
        cls.env['res.partner'].create([
            {'name': 'A', 'email': 'a@example.com'}, {'name': 'B', 'email': 'b@example.com'}])
        model = cls.env['ir.model']._get('res.partner')
        cls.profile = cls.env['dpa.profile'].create({
            'name': 'P', 'model_id': model.id,
            'field_ids': [Command.set(cls.env['ir.model.fields'].search(
                [('model_id', '=', model.id), ('name', '=', 'email')]).ids)],
        })

    def _report(self, user=None):
        self.profile.with_user(user or self.anna).action_run()
        return self.env['dpa.snapshot'].search([('profile_id', '=', self.profile.id), ('is_saved', '=', False)])

    def _saved(self):
        report = self._report()
        report.action_save_snapshot()
        return report

    def test_save_names_the_saver_as_author(self):
        """Requirements: FR-DPA-0018 (the analyst who saves is the author)."""
        report = self._report(self.anna)
        report.with_user(self.ben).action_save_snapshot()
        self.assertEqual(report.user_id, self.ben)
        self.assertEqual(report.origin, 'manual')

    def test_second_save_refused(self):
        """Requirements: FR-DPA-0018."""
        report = self._saved()
        with self.assertRaisesRegex(UserError, "already saved"):
            report.action_save_snapshot()

    def test_values_and_findings_refuse_change(self):
        """Requirements: FR-DPA-0018 (every line of a saved snapshot is frozen)."""
        report = self._saved()
        value = report.field_line_ids.value_ids[:1]
        self.assertTrue(value)
        with self.assertRaisesRegex(UserError, "cannot be changed"):
            value.write({'count': 99})
        with self.assertRaises(UserError):
            self.env['dpa.snapshot.finding'].create({
                'snapshot_id': report.id, 'kind': 'outdated', 'res_model': 'res.partner', 'res_id': 1})

    def test_no_new_line_on_saved_snapshot(self):
        """Requirements: FR-DPA-0018."""
        report = self._saved()
        with self.assertRaises(UserError):
            report.write({'field_line_ids': [Command.create({'field_name': 'x'})]})
        with self.assertRaises(UserError):
            report.field_line_ids[:1].unlink()

    def test_line_cannot_move_into_saved_snapshot(self):
        """Requirements: FR-DPA-0018 (re-linking a line into a saved snapshot is a change of it)."""
        saved = self._saved()
        open_report = self._report()
        line = open_report.field_line_ids[:1]
        with self.assertRaises(UserError):
            line.write({'snapshot_id': saved.id})
        with self.assertRaises(UserError):
            open_report.field_line_ids.value_ids[:1].write({'field_line_id': saved.field_line_ids[:1].id})

    def test_finding_cannot_move_into_saved_snapshot(self):
        """Requirements: FR-DPA-0018 (a finding is judged by its snapshot and by its field line)."""
        saved = self._saved()
        open_report = self._report()
        finding = self.env['dpa.snapshot.finding'].create({
            'snapshot_id': open_report.id, 'kind': 'outdated', 'res_model': 'res.partner', 'res_id': 1})
        with self.assertRaises(UserError):
            finding.write({'snapshot_id': saved.id})
        with self.assertRaises(UserError):
            finding.write({'field_line_id': saved.field_line_ids[:1].id})
        with self.assertRaises(UserError):
            self.env['dpa.snapshot.finding'].create({
                'snapshot_id': open_report.id, 'field_line_id': saved.field_line_ids[:1].id,
                'kind': 'outdated', 'res_model': 'res.partner', 'res_id': 1})

    def test_snapshot_can_be_created_already_saved(self):
        """Requirements: FR-DPA-0018 (a scheduled run writes a saved snapshot directly)."""
        snapshot = self.env['dpa.snapshot'].create({
            'name': 'S', 'model_name': 'res.partner', 'run_date': '2026-11-25 09:00:00',
            'is_saved': True, 'origin': 'scheduled',
            'field_line_ids': [Command.create({'field_name': 'email'})],
        })
        self.assertTrue(snapshot.is_saved)
        self.assertEqual(len(snapshot.field_line_ids), 1)
        with self.assertRaises(UserError):
            snapshot.write({'name': 'T'})

    def test_profile_with_unsaved_report_only_can_be_deleted(self):
        """Requirements: FR-DPA-0031."""
        self._report()
        self.profile.unlink()
        self.assertFalse(self.env['dpa.snapshot'].search([('profile_name', '=', 'P')]))
