import base64
import json

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import ConfigSnapshotCase


@tagged('post_install', '-at_install')
class TestImportCompare(ConfigSnapshotCase):
    """Import wizard: load a file and compare differences."""

    def _compare(self, settings=None, raw=None, name='prod.json'):
        if raw is None:
            raw = json.dumps({'format_version': 1, 'settings': settings}).encode()
        wiz = self.env['config.snapshot.import'].with_user(self.admin_user).create(
            {'file': base64.b64encode(raw), 'file_name': name})
        wiz.action_compare()
        return wiz

    def test_modified_added_removed(self):
        """Requirements: FR-TCM-006, SC-TCM-006-1, SC-TCM-006-2, SC-TCM-006-3"""
        self.env['config.snapshot.item'].sudo().search([]).unlink()
        param = self._param('t4.mod', 'b')
        old = self._param('t4.old', 'x')
        for p in (param, old):
            self.env['config.snapshot.item'].sudo().create({'parameter_id': p.id})
        wiz = self._compare([{'key': 't4.mod', 'value': 'a'}, {'key': 't4.new', 'value': 'n'}])
        kinds = {line.key: line.change_type for line in wiz.line_ids}
        self.assertEqual(kinds, {'t4.mod': 'modified', 't4.new': 'added', 't4.old': 'removed'})
        self.assertEqual(wiz.state, 'review')

    def test_sensitive_never_on_lines(self):
        """Requirements: FR-TCM-007, BR-TCM-002, SC-TCM-007-1, SC-TCM-007-3"""
        self._param('t4.secret', 'local-key')
        wiz = self._compare([
            {'key': 't4.secret', 'value': '********', 'sensitive': True},
            {'key': 't4.fresh', 'value': '********', 'sensitive': True}])
        self.assertEqual(wiz.line_ids.mapped('key'), ['t4.fresh'])
        self.assertFalse(wiz.line_ids.file_value)

    def test_refused_files(self):
        """Requirements: FR-TCM-005, FR-TCM-013, SC-TCM-005-2, SC-TCM-005-3"""
        before = self.env['config.snapshot.log'].search_count([('log_type', '=', 'import')])
        with self.assertRaisesRegex(UserError, 'not valid JSON'):
            self._compare(raw=b'not json')
        with self.assertRaisesRegex(UserError, 'not a configuration export'):
            self._compare(raw=b'{}')
        self.assertEqual(
            self.env['config.snapshot.log'].search_count([('log_type', '=', 'import')]), before)

    def test_import_logged(self):
        """Requirements: FR-TCM-013, SC-TCM-013-1"""
        self._compare([{'key': 't4.log', 'value': 'v'}])
        entry = self.env['config.snapshot.log'].search([('log_type', '=', 'import')], limit=1)
        self.assertEqual((entry.file_name, entry.added_count), ('prod.json', 1))
