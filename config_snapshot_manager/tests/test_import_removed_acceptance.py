import base64
import json

from odoo.tests.common import tagged

from .acceptance_base import AcceptanceBase


@tagged('post_install', '-at_install')
class TestImportRemovedAcceptance(AcceptanceBase):
    """Acceptance tests of T-009: delete removed settings only after confirmation (FR-TCM-010)."""

    def setUp(self):
        super().setUp()
        self.items = self.env['config.snapshot.item'].sudo()
        self.items.search([]).unlink()
        self.params = self.env['ir.config_parameter'].sudo()

    def _cover(self, key, value, sensitive=False):
        param = self._param(key, value)
        self.items.create({'parameter_id': param.id, 'sensitive': sensitive})
        return param

    def _compared_empty(self):
        raw = json.dumps({'format_version': 1, 'exported_on': '2026-01-01',
                          'settings': []}).encode()
        wiz = self.env['config.snapshot.import'].with_user(self.admin_user).create(
            {'file': base64.b64encode(raw), 'file_name': 'prod.json'})
        wiz.action_compare()
        return wiz

    def _exists(self, key):
        return bool(self.params.search([('key', '=', key)]))

    def _covered(self, key):
        return bool(self.items.search([('parameter_id.key', '=', key)]))

    def test_not_deleted_before_confirmation(self):
        """Requirements: FR-TCM-010, SC-TCM-010-1: compared, not yet confirmed: old.flag still exists."""
        self._cover('old.flag', 'x')
        wiz = self._compared_empty()
        self.assertEqual(wiz.removed_count, 1)
        self.assertTrue(self._exists('old.flag'))
        self.assertTrue(self._covered('old.flag'))

    def test_not_deleted_at_confirm_step(self):
        """Requirements: FR-TCM-010: reaching the confirmation screen still deletes nothing."""
        self._cover('old.flag', 'x')
        wiz = self._compared_empty()
        wiz.action_confirm()
        self.assertTrue(self._exists('old.flag'))

    def test_deleted_on_confirmed_apply(self):
        """Requirements: FR-TCM-010, SC-TCM-010-2: confirmed apply deletes old.flag and uncovers it."""
        self._cover('old.flag', 'x')
        wiz = self._compared_empty()
        wiz.action_confirm()
        wiz.action_apply()
        self.assertFalse(self._exists('old.flag'))
        self.assertFalse(self._covered('old.flag'))

    def test_sensitive_removed_deleted_the_same_way(self):
        """Requirements: FR-TCM-010: a sensitive setting listed as removed is deleted too."""
        self._cover('old.secret', 's3cret', sensitive=True)
        wiz = self._compared_empty()
        wiz.action_confirm()
        self.assertTrue(self._exists('old.secret'))
        wiz.action_apply()
        self.assertFalse(self._exists('old.secret'))
        self.assertFalse(self._covered('old.secret'))
