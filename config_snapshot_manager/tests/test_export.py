import base64
import json

from odoo.tests.common import tagged

from .acceptance_base import AcceptanceBase


@tagged('post_install', '-at_install')
class TestExport(AcceptanceBase):
    """Unit tests of the export wizard."""

    def test_two_secrets_and_plain(self):
        """Requirements: FR-TCM-004, BR-TCM-002, NFR-SEC-001: every secret masked in file."""
        self.env['config.snapshot.item'].sudo().search([]).unlink()
        for key, sens in (('x.one', True), ('x.two', True), ('x.three', False)):
            self.env['config.snapshot.item'].sudo().create(
                {'parameter_id': self._param(key, 'val-' + key).id, 'sensitive': sens})
        wiz = self.env['config.snapshot.export'].with_user(self.admin_user).create({})
        wiz.action_export()
        raw = base64.b64decode(wiz.file).decode()
        self.assertNotIn('val-x.one', raw)
        self.assertNotIn('val-x.two', raw)
        values = {s['key']: s['value'] for s in json.loads(raw)['settings']}
        self.assertEqual(values['x.three'], 'val-x.three')

    def test_bug_b_0090_empty_value_round_trip(self):
        """Regression test for B-0090: an empty setting is exported as null, not false.

        Requirements: FR-TCM-003, FR-TCM-005
        """
        self.env['config.snapshot.item'].sudo().search([]).unlink()
        param = self.env['ir.config_parameter'].sudo().create({'key': 'b90.empty'})
        self.assertFalse(param.value)
        self.env['config.snapshot.item'].sudo().create(
            {'parameter_id': param.id, 'sensitive': False})
        wiz = self.env['config.snapshot.export'].with_user(self.admin_user).create({})
        wiz.action_export()
        imp = self.env['config.snapshot.import'].with_user(self.admin_user).create(
            {'file': wiz.file, 'file_name': 'self.json'})
        imp.action_compare()
        self.assertFalse(imp.line_ids)
