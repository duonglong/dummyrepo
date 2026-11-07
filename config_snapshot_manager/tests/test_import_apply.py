import base64
import json

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import ConfigSnapshotCase


@tagged('post_install', '-at_install')
class TestImportApply(ConfigSnapshotCase):
    """Own tests of the confirm and apply steps of the import wizard."""

    def _wizard(self, settings):
        raw = json.dumps({'format_version': 1, 'settings': settings}).encode()
        wiz = self.env['config.snapshot.import'].with_user(self.admin_user).create(
            {'file': base64.b64encode(raw), 'file_name': 'f.json'})
        wiz.action_compare()
        return wiz

    def test_apply_needs_confirmation(self):
        """Requirements: FR-TCM-008, FR-TCM-009: apply without the confirm step is refused."""
        self.env['config.snapshot.item'].sudo().search([]).unlink()
        wiz = self._wizard([{'key': 'own.new', 'value': 'v', 'sensitive': False}])
        with self.assertRaises(UserError):
            wiz.action_apply()
        self.assertFalse(self.env['ir.config_parameter'].sudo().search([('key', '=', 'own.new')]))

    def test_apply_writes_one_log_entry_per_change(self):
        """Requirements: FR-TCM-014: one change entry per applied difference."""
        self.env['config.snapshot.item'].sudo().search([]).unlink()
        wiz = self._wizard([{'key': 'own.a', 'value': '1', 'sensitive': False},
                            {'key': 'own.b', 'value': '2', 'sensitive': False}])
        wiz.action_confirm()
        wiz.action_apply()
        self.env.flush_all()
        entries = self.env['config.snapshot.log'].sudo().search(
            [('log_type', '=', 'change'), ('key', 'in', ['own.a', 'own.b'])])
        self.assertEqual(len(entries), 2)
