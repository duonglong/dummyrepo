import base64
import contextlib
import json
import sys
from unittest.mock import patch

from lxml import etree

from odoo import models
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


@tagged('post_install', '-at_install')
class TestImportUx(ConfigSnapshotCase):
    """Regression tests of the UX findings of the import wizard."""

    def test_bug_b_0077_key_column_is_wide(self):
        """Regression test for B-0077: the Key column of the differences list is cut off.

        Requirements: NFR-USE-001
        """
        arch = self.env['config.snapshot.import'].get_view(view_type='form')['arch']
        key = etree.fromstring(arch).xpath('//field[@name="line_ids"]//list/field[@name="key"]')[0]
        width = int((key.get('width') or '0').replace('px', ''))
        self.assertGreaterEqual(width, 280, 'the Key column must be wide enough to read a key')

    def test_bug_b_0078_apply_notifies_counts(self):
        """Regression test for B-0078: apply gives no feedback after confirming.

        Requirements: FR-TCM-009
        """
        self.env['config.snapshot.item'].sudo().search([]).unlink()
        raw = json.dumps({'format_version': 1, 'settings': [
            {'key': 'own.ux1', 'value': '1', 'sensitive': False}]}).encode()
        wiz = self.env['config.snapshot.import'].with_user(self.admin_user).create(
            {'file': base64.b64encode(raw), 'file_name': 'f.json'})
        wiz.action_compare()
        wiz.action_confirm()
        action = wiz.action_apply()
        self.assertEqual(action.get('tag'), 'display_notification')
        self.assertIn('1', action['params']['message'])
        self.assertEqual(action['params']['next'], {'type': 'ir.actions.act_window_close'})


@tagged('post_install', '-at_install')
class TestImportNoSudo(ConfigSnapshotCase):
    """Regression test of the review finding on bare sudo."""

    def test_bug_b_0089_settings_handled_as_acting_admin(self):
        """Regression test for B-0089: settings are read and written without sudo.

        Requirements: FR-TCM-016, FR-TCM-009, NFR-SEC-001
        """
        watched = ('ir.config_parameter', 'config.snapshot.item')
        seen = []
        original = models.BaseModel._fetch_field

        def in_module(frame):
            # the ORM's own cascade searches run as superuser; only the module's calls count
            return 'config_snapshot_manager' in frame.f_code.co_filename

        def spy(name):
            real = getattr(models.BaseModel, name)

            def wrapper(self, *args, **kwargs):
                if self._name in watched and in_module(sys._getframe(1)):
                    seen.append((self._name, name, self.env.su))
                return real(self, *args, **kwargs)
            return wrapper

        def spy_fetch(self, field):
            if self._name in watched and in_module(sys._getframe(1)):
                seen.append((self._name, 'fetch', self.env.su))
            return original(self, field)

        self.env['config.snapshot.item'].sudo().search([]).unlink()
        old = self._param('b89.old', 'x')
        mod = self._param('b89.mod', 'a')
        for param in (old, mod):
            self.env['config.snapshot.item'].sudo().create({'parameter_id': param.id})
        raw = json.dumps({'format_version': 1, 'settings': [
            {'key': 'b89.mod', 'value': 'b'}, {'key': 'b89.new', 'value': 'n'}]}).encode()
        wiz = self.env['config.snapshot.import'].with_user(self.admin_user).create(
            {'file': base64.b64encode(raw), 'file_name': 'f.json'})
        export = self.env['config.snapshot.export'].with_user(self.admin_user).create({})
        names = ('create', 'write', 'unlink', 'search', 'search_read')
        with contextlib.ExitStack() as stack:
            for name in names:
                stack.enter_context(patch.object(models.BaseModel, name, spy(name)))
            stack.enter_context(patch.object(models.BaseModel, '_fetch_field', spy_fetch))
            wiz.action_compare()
            wiz.action_confirm()
            wiz.action_apply()
            export.action_export()
            self.env['config.snapshot.item'].with_user(self.admin_user).search([]).mapped(
                'value_display')
        self.assertTrue(seen)
        self.assertEqual([entry for entry in seen if entry[2]], [])
