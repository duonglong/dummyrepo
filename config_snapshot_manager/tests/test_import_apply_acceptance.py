import base64
import json
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import tagged

from .acceptance_base import AcceptanceBase

MASKED = '********'


@tagged('post_install', '-at_install')
class TestImportApplyAcceptance(AcceptanceBase):
    """Acceptance tests of T-005: confirm and apply the differences in one transaction."""

    def setUp(self):
        super().setUp()
        self.env['config.snapshot.item'].sudo().search([]).unlink()
        self.log = self.env['config.snapshot.log'].sudo()
        self.items = self.env['config.snapshot.item'].sudo()
        self.params = self.env['ir.config_parameter'].sudo()

    def _cover(self, key, value, sensitive=False):
        param = self._param(key, value)
        self.items.create({'parameter_id': param.id, 'sensitive': sensitive})
        return param

    @staticmethod
    def _entry(key, value, sensitive=False):
        return {'key': key, 'value': value, 'sensitive': sensitive}

    def _compared(self, settings):
        raw = json.dumps({'format_version': 1, 'exported_on': '2026-01-01',
                          'settings': settings}).encode()
        wiz = self.env['config.snapshot.import'].with_user(self.admin_user).create(
            {'file': base64.b64encode(raw), 'file_name': 'prod.json'})
        wiz.action_compare()
        return wiz

    def _value(self, key):
        self.env.invalidate_all()
        return self.params.search([('key', '=', key)]).value

    def _exists(self, key):
        return bool(self.params.search([('key', '=', key)]))

    def _changes(self, key=None):
        self.env.flush_all()
        domain = [('log_type', '=', 'change')]
        if key:
            domain.append(('key', '=', key))
        return self.log.search(domain)

    def test_confirm_counts(self):
        """Requirements: FR-TCM-008, SC-TCM-008-1: confirmation states the counts, nothing applied."""
        self._cover('m.acc', 'a')
        self._cover('r.acc', 'a')
        wiz = self._compared([self._entry('m.acc', 'b'), self._entry('n.acc', 'c')])
        action = wiz.action_confirm()
        self.assertEqual(wiz.state, 'confirm')
        texts = [str(action)]
        for name, field in wiz._fields.items():
            if field.type in ('char', 'text', 'html'):
                texts.append(str(wiz[name] or ''))
        self.assertIn('1 added, 1 modified, 1 removed', ' '.join(texts))
        self.assertEqual(self._value('m.acc'), 'a')
        self.assertTrue(self._exists('r.acc'))
        self.assertFalse(self._exists('n.acc'))

    def test_cancel_changes_nothing(self):
        """Requirements: FR-TCM-008, SC-TCM-008-2: cancel leaves every setting and the log as is."""
        self._cover('m.acc', 'a')
        wiz = self._compared([self._entry('m.acc', 'b'), self._entry('n.acc', 'c')])
        wiz.action_confirm()
        before = self._changes()
        wiz.action_cancel()
        self.assertEqual(self._value('m.acc'), 'a')
        self.assertFalse(self._exists('n.acc'))
        self.assertEqual(self._changes(), before)

    def test_apply_added_and_modified(self):
        """Requirements: FR-TCM-009, SC-TCM-009-1: added is created and covered, modified written."""
        self._cover('web.base.url.acc', 'https://b.example')
        wiz = self._compared([self._entry('web.base.url.acc', 'https://a.example'),
                              self._entry('report.url.acc', 'http://r')])
        wiz.action_confirm()
        wiz.action_apply()
        self.assertEqual(self._value('web.base.url.acc'), 'https://a.example')
        self.assertEqual(self._value('report.url.acc'), 'http://r')
        item = self.items.search([('parameter_id.key', '=', 'report.url.acc')])
        self.assertEqual(len(item), 1)
        self.assertFalse(item.sensitive)

    def test_apply_added_sensitive_has_no_value(self):
        """Requirements: FR-TCM-007, FR-TCM-009, BR-TCM-002: a new secret is created empty, flagged."""
        wiz = self._compared([self._entry('new.secret.acc', MASKED, True)])
        wiz.action_confirm()
        wiz.action_apply()
        self.assertTrue(self._exists('new.secret.acc'))
        self.assertFalse(self._value('new.secret.acc'))
        item = self.items.search([('parameter_id.key', '=', 'new.secret.acc')])
        self.assertTrue(item.sensitive)

    def test_apply_covers_uncovered_file_keys(self):
        """Requirements: FR-TCM-009: after apply the settings named in the file are covered."""
        self._param('uncov.acc', 'here')
        wiz = self._compared([self._entry('uncov.acc', 'there')])
        wiz.action_confirm()
        wiz.action_apply()
        self.assertEqual(self._value('uncov.acc'), 'there')
        self.assertEqual(self.items.search_count([('parameter_id.key', '=', 'uncov.acc')]), 1)

    def test_apply_atomic(self):
        """Requirements: FR-TCM-009, NFR-REL-001, SC-TCM-009-2: a failing write undoes all."""
        self._cover('first.acc', 'old1')
        self._cover('second.acc', 'old2')
        wiz = self._compared([self._entry('first.acc', 'new1'),
                              self._entry('second.acc', 'boom-value')])
        wiz.action_confirm()
        model = type(self.params)
        original = model.write

        def failing_write(records, vals):
            if vals.get('value') == 'boom-value':
                raise ValueError('simulated failure')
            return original(records, vals)

        with patch.object(model, 'write', failing_write):
            with self.assertRaises(UserError):
                wiz.action_apply()
        self.assertEqual(self._value('first.acc'), 'old1')
        self.assertEqual(self._value('second.acc'), 'old2')
        self.assertFalse(self._changes())

    def test_removed_only_after_apply(self):
        """Requirements: FR-TCM-010, SC-TCM-010-1, SC-TCM-010-2: delete only on confirmed apply."""
        self._cover('old.flag.acc', 'x')
        wiz = self._compared([])
        wiz.action_confirm()
        self.assertTrue(self._exists('old.flag.acc'))
        wiz.action_apply()
        self.assertFalse(self._exists('old.flag.acc'))
        self.assertFalse(self.items.search([('parameter_id.key', '=', 'old.flag.acc')]))

    def test_stale_refused(self):
        """Requirements: FR-TCM-011, SC-TCM-011-1: a changed setting refuses the apply."""
        self._cover('web.base.url.acc', 'https://b.example')
        self._cover('other.acc', 'o')
        wiz = self._compared([self._entry('web.base.url.acc', 'https://a.example'),
                              self._entry('other.acc', 'p')])
        wiz.action_confirm()
        self.params.search([('key', '=', 'web.base.url.acc')]).write({'value': 'https://c.example'})
        with self.assertRaisesRegex(UserError, 'Compare again'):
            wiz.action_apply()
        self.assertEqual(self._value('web.base.url.acc'), 'https://c.example')
        self.assertEqual(self._value('other.acc'), 'o')
        self.assertFalse(self._changes())

    def test_stale_added_key_created_meanwhile(self):
        """Requirements: FR-TCM-011: an added key that now exists refuses the apply."""
        wiz = self._compared([self._entry('late.acc', 'v')])
        wiz.action_confirm()
        self._param('late.acc', 'someone')
        with self.assertRaisesRegex(UserError, 'Compare again'):
            wiz.action_apply()
        self.assertEqual(self._value('late.acc'), 'someone')

    def test_stale_removed_key_deleted_meanwhile(self):
        """Requirements: FR-TCM-011: a removed key that vanished refuses the apply."""
        param = self._cover('vanish.acc', 'v')
        wiz = self._compared([])
        wiz.action_confirm()
        param.unlink()
        with self.assertRaisesRegex(UserError, 'Compare again'):
            wiz.action_apply()

    def test_change_log_values(self):
        """Requirements: FR-TCM-014, SC-TCM-014-1: one entry holds the old and the new value."""
        self._cover('web.base.url.acc', 'https://b.example')
        wiz = self._compared([self._entry('web.base.url.acc', 'https://a.example')])
        wiz.action_confirm()
        wiz.action_apply()
        entries = self._changes('web.base.url.acc')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries.change_type, 'modified')
        self.assertEqual(entries.old_value, 'https://b.example')
        self.assertEqual(entries.new_value, 'https://a.example')
        self.assertEqual(entries.user_id, self.admin_user)

    def test_change_log_masked(self):
        """Requirements: FR-TCM-014, NFR-SEC-001, BR-TCM-001, SC-TCM-014-2: secret masked in log."""
        self._cover('old.secret.acc', 's3cret', sensitive=True)
        wiz = self._compared([])
        wiz.action_confirm()
        wiz.action_apply()
        self.assertFalse(self._exists('old.secret.acc'))
        entries = self._changes('old.secret.acc')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries.change_type, 'removed')
        self.assertEqual(entries.old_value, MASKED)
        for entry in self._changes():
            for value in (entry.key, entry.old_value, entry.new_value):
                self.assertNotIn('s3cret', value or '')

    def test_sensitive_existing_value_kept(self):
        """Requirements: BR-TCM-002, FR-TCM-007: an existing secret is not overwritten."""
        self._cover('keep.secret.acc', 'local-key', sensitive=True)
        wiz = self._compared([self._entry('keep.secret.acc', MASKED, True)])
        wiz.action_confirm()
        wiz.action_apply()
        self.assertEqual(self._value('keep.secret.acc'), 'local-key')
        item = self.items.search([('parameter_id.key', '=', 'keep.secret.acc')])
        self.assertTrue(item.sensitive)

    def test_non_admin_refused(self):
        """Requirements: FR-TCM-016: a non-administrator cannot apply."""
        self._cover('m.acc', 'a')
        wiz = self._compared([self._entry('m.acc', 'b')])
        wiz.action_confirm()
        with self.assertRaises(AccessError):
            wiz.with_user(self.plain_user).action_apply()
        self.assertEqual(self._value('m.acc'), 'a')
