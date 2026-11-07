from odoo import SUPERUSER_ID
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import tagged

from ..const import MASK
from .common import ConfigSnapshotCase


@tagged('post_install', '-at_install')
class TestLog(ConfigSnapshotCase):
    """Requirements: FR-TCM-015, FR-TCM-016, NFR-SEC-001"""

    def _log(self, user=None):
        return self.env['config.snapshot.log'].with_user(user or self.admin_user)

    def test_record_sets_user(self):
        entry = self._log()._record({'log_type': 'export', 'setting_count': 3})
        self.assertEqual(entry.user_id, self.admin_user)
        self.assertEqual(entry.setting_count, 3)

    def test_write_refused(self):
        entry = self._log()._record({'log_type': 'export'})
        for user in (self.admin_user.id, SUPERUSER_ID):
            with self.assertRaises(UserError):
                entry.with_user(user).write({'setting_count': 2})

    def test_unlink_refused(self):
        entry = self._log()._record({'log_type': 'export'})
        for user in (self.admin_user.id, SUPERUSER_ID):
            with self.assertRaises(UserError):
                entry.with_user(user).unlink()
        self.assertTrue(entry.exists())

    def test_sensitive_masked(self):
        entry = self._log()._record(
            {'log_type': 'change', 'key': 'k', 'old_value': 's3cret', 'new_value': 'n3w'},
            sensitive=True)
        self.assertEqual((entry.old_value, entry.new_value), (MASK, MASK))

    def test_filter_by_type(self):
        self._log()._record({'log_type': 'export', 'file_name': 'tl_x.json'})
        imp = self._log()._record({'log_type': 'import', 'file_name': 'tl_x.json'})
        found = self._log().search(
            [('log_type', '=', 'import'), ('file_name', '=', 'tl_x.json')])
        self.assertEqual(found, imp)

    def test_non_admin_refused(self):
        with self.assertRaises(AccessError):
            self._log(self.plain_user).search_count([])
