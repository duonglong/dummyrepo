from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSnapshotFrozen(TransactionCase):
    """Own tests of T-007: the lines and values of a saved snapshot never change."""

    def _snapshot(self):
        model = self.env['ir.model']._get('res.partner')
        profile = self.env['dpa.profile'].create({'name': 'P', 'model_id': model.id})
        profile.action_run()
        report = profile.snapshot_ids.browse() or self.env['dpa.snapshot'].search([('profile_id', '=', profile.id)])
        report.action_save_snapshot()
        return profile, report

    def test_values_and_findings_refuse_change(self):
        """Requirements: FR-DPA-0018 (every line of a saved snapshot is frozen)."""
        profile, report = self._snapshot()
        value = report.field_line_ids.value_ids[:1]
        if value:
            with self.assertRaises(UserError):
                value.write({'count': 99})
        with self.assertRaises(UserError):
            self.env['dpa.snapshot.finding'].create({
                'snapshot_id': report.id, 'kind': 'outdated', 'res_model': 'res.partner', 'res_id': 1})

    def test_no_new_line_on_saved_snapshot(self):
        """Requirements: FR-DPA-0018."""
        profile, report = self._snapshot()
        with self.assertRaises(UserError):
            report.write({'field_line_ids': [(0, 0, {'field_name': 'x'})]})
        with self.assertRaises(UserError):
            report.field_line_ids[:1].unlink()

    def test_profile_with_unsaved_report_only_can_be_deleted(self):
        """Requirements: FR-DPA-0031."""
        model = self.env['ir.model']._get('res.partner')
        profile = self.env['dpa.profile'].create({'name': 'P', 'model_id': model.id})
        profile.action_run()
        profile.unlink()
        self.assertFalse(self.env['dpa.snapshot'].search([('profile_name', '=', 'P')]))
