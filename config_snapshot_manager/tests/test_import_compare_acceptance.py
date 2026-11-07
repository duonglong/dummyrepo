import base64
import json
import time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import tagged

from .acceptance_base import AcceptanceBase


@tagged('post_install', '-at_install')
class TestImportCompareAcceptance(AcceptanceBase):
    """Acceptance tests of T-004: load an import file and compare differences."""

    def setUp(self):
        super().setUp()
        self.env['config.snapshot.item'].sudo().search([]).unlink()
        self.log = self.env['config.snapshot.log'].sudo()

    def _cover(self, key, value, sensitive=False):
        param = self._param(key, value)
        self.env['config.snapshot.item'].sudo().create(
            {'parameter_id': param.id, 'sensitive': sensitive})
        return param

    def _file(self, settings, raw=None):
        if raw is None:
            raw = json.dumps({'format_version': 1, 'exported_on': '2026-01-01',
                              'settings': settings}).encode()
        return base64.b64encode(raw)

    def _wizard(self, settings=None, raw=None, name='prod.json', user=None):
        return self.env['config.snapshot.import'].with_user(user or self.admin_user).create(
            {'file': self._file(settings, raw), 'file_name': name})

    def _compare(self, settings=None, raw=None, name='prod.json'):
        wiz = self._wizard(settings, raw, name)
        wiz.action_compare()
        return wiz

    @staticmethod
    def _entry(key, value, sensitive=False):
        return {'key': key, 'value': value, 'sensitive': sensitive}

    def _line(self, wiz, key):
        return wiz.line_ids.filtered(lambda line: line.key == key)

    def test_valid_file_lists_diff(self):
        """Requirements: FR-TCM-005, SC-TCM-005-1: a valid file moves to review with lines."""
        self._param('web.base.url.acc', 'https://b.example')
        wiz = self._compare([self._entry('web.base.url.acc', 'https://a.example')])
        self.assertEqual(wiz.state, 'review')
        self.assertEqual(len(wiz.line_ids), 1)

    def test_invalid_json_refused(self):
        """Requirements: FR-TCM-005, FR-TCM-013, SC-TCM-005-2, SC-TCM-013-2: bad JSON refused."""
        before = self.log.search_count([('log_type', '=', 'import')])
        wiz = self._wizard(raw=b'not json')
        with self.assertRaisesRegex(UserError, 'not valid JSON'):
            wiz.action_compare()
        self.assertFalse(wiz.line_ids)
        self.assertEqual(self.log.search_count([('log_type', '=', 'import')]), before)

    def test_missing_version_refused(self):
        """Requirements: FR-TCM-005, SC-TCM-005-3: JSON without format_version is refused."""
        before = self.log.search_count([('log_type', '=', 'import')])
        wiz = self._wizard(raw=b'{}')
        with self.assertRaisesRegex(UserError, 'not a configuration export'):
            wiz.action_compare()
        self.assertFalse(wiz.line_ids)
        self.assertEqual(self.log.search_count([('log_type', '=', 'import')]), before)

    def test_missing_settings_list_refused(self):
        """Requirements: FR-TCM-005: JSON with a version but no settings list is refused."""
        wiz = self._wizard(raw=b'{"format_version": 1, "settings": "x"}')
        with self.assertRaisesRegex(UserError, 'not a configuration export'):
            wiz.action_compare()

    def test_modified(self):
        """Requirements: FR-TCM-006, SC-TCM-006-1: different values give a modified line."""
        self._cover('web.base.url.acc', 'https://b.example')
        wiz = self._compare([self._entry('web.base.url.acc', 'https://a.example')])
        line = self._line(wiz, 'web.base.url.acc')
        self.assertEqual(len(line), 1)
        self.assertEqual(line.change_type, 'modified')
        self.assertEqual(line.current_value, 'https://b.example')
        self.assertEqual(line.file_value, 'https://a.example')

    def test_added(self):
        """Requirements: FR-TCM-006, SC-TCM-006-2: a key absent here is added."""
        wiz = self._compare([self._entry('new.key.acc', 'v')])
        line = self._line(wiz, 'new.key.acc')
        self.assertEqual(line.change_type, 'added')
        self.assertEqual(line.file_value, 'v')
        self.assertFalse(line.parameter_id)

    def test_removed(self):
        """Requirements: FR-TCM-006, SC-TCM-006-3: a covered setting not in the file is removed."""
        self._cover('old.key.acc', 'x')
        wiz = self._compare([])
        line = self._line(wiz, 'old.key.acc')
        self.assertEqual(line.change_type, 'removed')
        self.assertEqual(line.current_value, 'x')

    def test_equal_not_listed(self):
        """Requirements: FR-TCM-006, SC-TCM-006-4: equal values give no line."""
        self._cover('same.acc', 'v')
        wiz = self._compare([self._entry('same.acc', 'v')])
        self.assertFalse(self._line(wiz, 'same.acc'))

    def test_uncovered_existing_compared(self):
        """Requirements: FR-TCM-006: an existing uncovered setting is compared when in the file."""
        self._param('uncov.acc', 'here')
        wiz = self._compare([self._entry('uncov.acc', 'there')])
        self.assertEqual(self._line(wiz, 'uncov.acc').change_type, 'modified')

    def test_sensitive_target_kept(self):
        """Requirements: FR-TCM-007, BR-TCM-002, SC-TCM-007-1, SC-TCM-007-2: secret kept, hidden."""
        self._cover('mail.api.key.acc', 'local-key', sensitive=True)
        wiz = self._compare([self._entry('mail.api.key.acc', '********', True)])
        self.assertFalse(self._line(wiz, 'mail.api.key.acc'))
        for line in wiz.line_ids:
            for field in ('key', 'current_value', 'file_value'):
                self.assertNotIn('local-key', line[field] or '')

    def test_sensitive_in_file_only_kept(self):
        """Requirements: FR-TCM-007: secret in the file only still keeps the value here."""
        self._param('f.secret.acc', 'local-key')
        wiz = self._compare([self._entry('f.secret.acc', '********', True)])
        self.assertFalse(self._line(wiz, 'f.secret.acc'))

    def test_sensitive_here_only_kept(self):
        """Requirements: FR-TCM-007: secret here only still keeps the value here."""
        self._cover('h.secret.acc', 'local-key', sensitive=True)
        wiz = self._compare([self._entry('h.secret.acc', 'other', False)])
        self.assertFalse(self._line(wiz, 'h.secret.acc'))

    def test_sensitive_added_no_value(self):
        """Requirements: FR-TCM-007, SC-TCM-007-3: a missing secret is added with no value."""
        wiz = self._compare([self._entry('new.secret.acc', '********', True)])
        line = self._line(wiz, 'new.secret.acc')
        self.assertEqual(line.change_type, 'added')
        self.assertFalse(line.file_value)

    def test_import_logged(self):
        """Requirements: FR-TCM-013, SC-TCM-013-1: one import entry with the counts."""
        self._cover('m1.acc', 'a')
        self._cover('m2.acc', 'b')
        before = self.log.search([('log_type', '=', 'import')])
        self._compare([self._entry('m1.acc', 'x'), self._entry('m2.acc', 'y'),
                       self._entry('n1.acc', 'z')], name='prod.json')
        new = self.log.search([('log_type', '=', 'import')]) - before
        self.assertEqual(len(new), 1)
        self.assertEqual(new.file_name, 'prod.json')
        self.assertEqual(new.user_id, self.admin_user)
        self.assertEqual((new.added_count, new.modified_count, new.removed_count), (1, 2, 0))

    def test_no_differences(self):
        """Requirements: FR-TCM-006, SC-TCM-008-3: a matching file has no difference."""
        self._cover('same.acc', 'v')
        wiz = self._compare([self._entry('same.acc', 'v')])
        self.assertEqual(wiz.difference_count, 0)
        self.assertEqual(wiz.state, 'review')

    def test_counts(self):
        """Requirements: FR-TCM-006: header counts match the lines."""
        self._cover('m.acc', 'a')
        self._cover('r.acc', 'a')
        wiz = self._compare([self._entry('m.acc', 'b'), self._entry('n.acc', 'c')])
        self.assertEqual((wiz.added_count, wiz.modified_count, wiz.removed_count), (1, 1, 1))
        self.assertEqual(wiz.difference_count, 3)

    def test_500_settings_fast(self):
        """Requirements: NFR-PERF-001: 500 settings compare in under 5 seconds."""
        entries = []
        for i in range(500):
            self._param('bulk.acc.%d' % i, 'here')
            entries.append(self._entry('bulk.acc.%d' % i, 'there'))
        wiz = self._wizard(entries)
        start = time.monotonic()
        wiz.action_compare()
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(wiz.modified_count, 500)

    def test_settings_unchanged_after_compare(self):
        """Requirements: FR-TCM-005, BR-TCM-002: comparing changes no setting."""
        self._cover('keep.acc', 'a')
        self._cover('gone.acc', 'a')
        params = self.env['ir.config_parameter'].sudo()
        before = {p.key: p.value for p in params.search([])}
        self._compare([self._entry('keep.acc', 'b'), self._entry('new.acc', 'c')])
        after = {p.key: p.value for p in params.search([])}
        self.assertEqual(before, after)

    def test_non_admin_refused(self):
        """Requirements: FR-TCM-016: a non-administrator cannot compare."""
        with self.assertRaises(AccessError):
            wiz = self._wizard([], user=self.plain_user)
            wiz.action_compare()
