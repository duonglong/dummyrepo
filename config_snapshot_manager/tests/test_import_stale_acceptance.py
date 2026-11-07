import base64
import json

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .acceptance_base import AcceptanceBase


@tagged('post_install', '-at_install')
class TestImportStaleAcceptance(AcceptanceBase):
    """Acceptance tests of T-010: refuse a stale comparison (FR-TCM-011)."""

    def setUp(self):
        super().setUp()
        self.items = self.env['config.snapshot.item'].sudo()
        self.items.search([]).unlink()
        self.params = self.env['ir.config_parameter'].sudo()
        self.log = self.env['config.snapshot.log'].sudo()

    def _set_url(self, value):
        param = self.params.search([('key', '=', 'web.base.url')])
        if param:
            param.write({'value': value})
        else:
            param = self._param('web.base.url', value)
        return param

    def _compared_url(self, file_value):
        raw = json.dumps({
            'format_version': 1, 'exported_on': '2026-01-01',
            'settings': [{'key': 'web.base.url', 'value': file_value, 'sensitive': False}],
        }).encode()
        wiz = self.env['config.snapshot.import'].with_user(self.admin_user).create(
            {'file': base64.b64encode(raw), 'file_name': 'prod.json'})
        wiz.action_compare()
        return wiz

    def _url(self):
        self.env.invalidate_all()
        return self.params.search([('key', '=', 'web.base.url')]).value

    def test_stale_modified_refused(self):
        """Requirements: FR-TCM-011, SC-TCM-011-1: a setting changed after the comparison
        refuses the apply with a message to compare again and keeps the newer value."""
        param = self._set_url('https://b.example')
        self.items.create({'parameter_id': param.id})
        wiz = self._compared_url('https://a.example')
        self.assertEqual(wiz.modified_count, 1)
        wiz.action_confirm()
        self._set_url('https://c.example')
        with self.assertRaisesRegex(UserError, '(?i)compare again'):
            wiz.action_apply()
        self.assertEqual(self._url(), 'https://c.example')

    def test_stale_refusal_writes_no_change_log(self):
        """Requirements: FR-TCM-011: a refused apply changes nothing, so no change is logged."""
        param = self._set_url('https://b.example')
        self.items.create({'parameter_id': param.id})
        wiz = self._compared_url('https://a.example')
        wiz.action_confirm()
        self._set_url('https://c.example')
        with self.assertRaises(UserError):
            wiz.action_apply()
        self.env.flush_all()
        self.assertFalse(self.log.search([('log_type', '=', 'change')]))

    def test_unchanged_setting_still_applies(self):
        """Requirements: FR-TCM-011: boundary, no change since the comparison, so apply works."""
        param = self._set_url('https://b.example')
        self.items.create({'parameter_id': param.id})
        wiz = self._compared_url('https://a.example')
        wiz.action_confirm()
        wiz.action_apply()
        self.assertEqual(self._url(), 'https://a.example')
