from odoo import SUPERUSER_ID
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import tagged

from .acceptance_base import AcceptanceBase


@tagged('post_install', '-at_install')
class TestLogAcceptance(AcceptanceBase):
    """Acceptance tests of T-002: the append-only audit log."""

    def _log(self, user=None):
        return self.env['config.snapshot.log'].with_user(user or self.admin_user)

    def test_record_sets_user(self):
        """Requirements: FR-TCM-015: an entry holds the user who made it."""
        entry = self._log()._record({'log_type': 'export', 'setting_count': 3})
        self.assertEqual(entry.user_id, self.admin_user)
        self.assertEqual(entry.setting_count, 3)
        self.assertTrue(entry.date)

    def test_write_refused(self):
        """Requirements: FR-TCM-015, SC-TCM-015-1: an entry cannot be edited."""
        entry = self._log()._record({'log_type': 'export', 'setting_count': 1})
        for user in (self.admin_user.id, SUPERUSER_ID):
            with self.assertRaises(UserError):
                entry.with_user(user).write({'setting_count': 9})
        with self.assertRaises(UserError):
            entry.sudo().write({'setting_count': 9})

    def test_unlink_refused(self):
        """Requirements: FR-TCM-015, SC-TCM-015-1: an entry cannot be deleted."""
        entry = self._log()._record({'log_type': 'export', 'setting_count': 1})
        for user in (self.admin_user.id, SUPERUSER_ID):
            with self.assertRaises(UserError):
                entry.with_user(user).unlink()
        self.assertTrue(entry.exists())

    def test_direct_create_refused(self):
        """Requirements: FR-TCM-015: only the tool writes entries, not a user's create."""
        with self.assertRaises(AccessError):
            self._log().create({'log_type': 'export', 'user_id': self.admin_user.id})

    def test_sensitive_masked(self):
        """Requirements: BR-TCM-001, NFR-SEC-001: sensitive values are never stored."""
        entry = self._log()._record(
            {'log_type': 'change', 'key': 'k', 'old_value': 's3cret', 'new_value': 'n3w'},
            sensitive=True)
        self.assertEqual(entry.old_value, '********')
        self.assertEqual(entry.new_value, '********')
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT 1 FROM config_snapshot_log WHERE id = %s AND "
            "(old_value LIKE '%%s3cret%%' OR new_value LIKE '%%n3w%%')", (entry.id,))
        self.assertFalse(self.env.cr.fetchone())

    def test_not_sensitive_kept(self):
        """Requirements: FR-TCM-014: a plain value is stored as given."""
        entry = self._log()._record(
            {'log_type': 'change', 'key': 'k', 'old_value': 'a', 'new_value': 'b',
             'change_type': 'modified'})
        self.assertEqual((entry.old_value, entry.new_value), ('a', 'b'))

    def test_filter_by_type(self):
        """Requirements: FR-TCM-015, SC-TCM-015-2: filter the log by type."""
        self._log()._record({'log_type': 'export', 'setting_count': 1})
        imp = self._log()._record({'log_type': 'import', 'file_name': 'acc_prod.json'})
        found = self._log().search([('log_type', '=', 'import'),
                                    ('file_name', '=', 'acc_prod.json')])
        self.assertEqual(found, imp)
        self.assertFalse(self._log().search([('log_type', '=', 'export'),
                                             ('file_name', '=', 'acc_prod.json')]))

    def test_non_admin_refused(self):
        """Requirements: FR-TCM-016, SC-TCM-016-1: a non-administrator cannot read the log."""
        with self.assertRaises(AccessError):
            self._log(self.plain_user).search_count([])
