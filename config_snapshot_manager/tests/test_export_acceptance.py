import base64
import json
import re

from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from .acceptance_base import AcceptanceBase


@tagged('post_install', '-at_install')
class TestExportAcceptance(AcceptanceBase):
    """Acceptance tests of T-003: export covered settings to JSON."""

    def setUp(self):
        super().setUp()
        # start from an empty covered list so counts are exact
        self.env['config.snapshot.item'].sudo().search([]).unlink()

    def _cover(self, key, value, sensitive=False):
        param = self._param(key, value)
        return self.env['config.snapshot.item'].sudo().create(
            {'parameter_id': param.id, 'sensitive': sensitive})

    def _export(self):
        wiz = self.env['config.snapshot.export'].with_user(self.admin_user).create({})
        action = wiz.action_export()
        return wiz, action

    def _payload(self, wiz):
        return json.loads(base64.b64decode(wiz.file).decode('utf-8'))

    def test_export_two_settings(self):
        """Requirements: FR-TCM-003, SC-TCM-003-1: two plain settings are exported with values."""
        self._cover('web.base.url.acc', 'https://a.example')
        self._cover('report.url.acc', 'http://r')
        wiz, action = self._export()
        self.assertEqual(action.get('type'), 'ir.actions.act_window')
        data = self._payload(wiz)
        self.assertEqual(data['format_version'], 1)
        self.assertTrue(data['exported_on'])
        self.assertEqual(data['settings'], [
            {'key': 'report.url.acc', 'value': 'http://r', 'sensitive': False},
            {'key': 'web.base.url.acc', 'value': 'https://a.example', 'sensitive': False},
        ])
        self.assertEqual(wiz.setting_count, 2)

    def test_export_empty(self):
        """Requirements: FR-TCM-003, SC-TCM-003-2: an empty covered list gives an empty file."""
        wiz, _action = self._export()
        self.assertEqual(self._payload(wiz)['settings'], [])

    def test_uncovered_not_exported(self):
        """Requirements: FR-TCM-003, SC-TCM-003-3: uncovered settings are never exported."""
        self._cover('web.base.url.acc', 'https://a.example')
        self._param('uncovered.acc', 'x')
        wiz, _action = self._export()
        keys = [s['key'] for s in self._payload(wiz)['settings']]
        self.assertEqual(keys, ['web.base.url.acc'])

    def test_secret_masked(self):
        """Requirements: FR-TCM-004, BR-TCM-001, BR-TCM-002, SC-TCM-004-1: secret never in file."""
        self._cover('mail.api.key.acc', 's3cret', sensitive=True)
        wiz, _action = self._export()
        raw = base64.b64decode(wiz.file).decode('utf-8')
        self.assertNotIn('s3cret', raw)
        self.assertEqual(self._payload(wiz)['settings'], [
            {'key': 'mail.api.key.acc', 'value': '********', 'sensitive': True}])

    def test_export_logged(self):
        """Requirements: FR-TCM-012, SC-TCM-012-1: one export entry with the count, no values."""
        self._cover('a.acc', '1')
        self._cover('b.acc', '2')
        self._cover('c.acc', 's3cret', sensitive=True)
        log = self.env['config.snapshot.log'].sudo()
        before = log.search([('log_type', '=', 'export')])
        self._export()
        new = log.search([('log_type', '=', 'export')]) - before
        self.assertEqual(len(new), 1)
        self.assertEqual(new.user_id, self.admin_user)
        self.assertEqual(new.setting_count, 3)
        self.assertFalse(new.old_value)
        self.assertFalse(new.new_value)

    def test_file_name(self):
        """Requirements: FR-TCM-003: file name is <database>_config_<date>.json."""
        wiz, _action = self._export()
        expected = re.escape(self.env.cr.dbname) + r'_config_\d{4}-\d{2}-\d{2}\.json'
        self.assertRegex(wiz.file_name, '^' + expected + '$')

    def test_non_admin_refused(self):
        """Requirements: FR-TCM-016: a non-administrator cannot export."""
        with self.assertRaises(AccessError):
            wiz = self.env['config.snapshot.export'].with_user(self.plain_user).create({})
            wiz.action_export()
