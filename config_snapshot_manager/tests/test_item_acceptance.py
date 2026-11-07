from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .acceptance_base import AcceptanceBase


@tagged('post_install', '-at_install')
class TestItemAcceptance(AcceptanceBase):
    """Acceptance tests of T-001: the covered settings list."""

    def _items(self, user=None):
        return self.env['config.snapshot.item'].with_user(user or self.admin_user)

    def test_add_covered(self):
        """Requirements: FR-TCM-001, SC-TCM-001-1: an existing setting can be covered."""
        param = self._param('web.base.url.acc', 'https://a.example')
        item = self._items().create({'parameter_id': param.id})
        self.assertEqual(item.key, 'web.base.url.acc')
        self.assertIn(item, self._items().search([]))

    def test_remove_keeps_param(self):
        """Requirements: FR-TCM-001, SC-TCM-001-2: removing from the list keeps the setting."""
        param = self._param('web.base.url.acc', 'https://a.example')
        item = self._items().create({'parameter_id': param.id})
        item.unlink()
        self.assertFalse(item.exists())
        self.assertTrue(param.exists())
        self.assertEqual(param.value, 'https://a.example')

    def test_duplicate_refused(self):
        """Requirements: FR-TCM-001, BR-TCM-003, SC-TCM-001-3: a setting is covered once."""
        param = self._param('dup.acc', '1')
        self._items().create({'parameter_id': param.id})
        with self.assertRaises((IntegrityError, ValidationError)) as ctx, \
                mute_logger('odoo.sql_db'), self.cr.savepoint():
            self._items().create({'parameter_id': param.id})
        if isinstance(ctx.exception, ValidationError):
            self.assertIn('already covered', str(ctx.exception))

    def test_sensitive_default_false(self):
        """Requirements: FR-TCM-002, SC-TCM-002-2: sensitive is off by default."""
        param = self._param('flag.acc', '1')
        item = self._items().create({'parameter_id': param.id})
        self.assertFalse(item.sensitive)

    def test_sensitive_flag_stored(self):
        """Requirements: FR-TCM-002, SC-TCM-002-1: marking a setting sensitive is stored."""
        param = self._param('mail.api.key.acc', 'k')
        item = self._items().create({'parameter_id': param.id})
        item.write({'sensitive': True})
        self.env.flush_all()
        item.invalidate_recordset()
        self.assertTrue(item.sensitive)

    def test_value_display_masked(self):
        """Requirements: BR-TCM-001, FR-TCM-002: a sensitive value shows as the mask."""
        param = self._param('secret.acc', 's3cret')
        item = self._items().create({'parameter_id': param.id, 'sensitive': True})
        self.assertEqual(item.value_display, '********')
        plain = self._items().create({
            'parameter_id': self._param('plain.acc', 'visible').id})
        self.assertEqual(plain.value_display, 'visible')

    def test_parameter_delete_removes_item(self):
        """Requirements: FR-TCM-001: deleting the setting drops its covered line."""
        param = self._param('gone.acc', '1')
        item = self._items().create({'parameter_id': param.id})
        param.unlink()
        self.assertFalse(item.exists())

    def test_non_admin_refused(self):
        """Requirements: FR-TCM-016, SC-TCM-016-1: a non-administrator is refused."""
        param = self._param('nope.acc', '1')
        with self.assertRaises(AccessError):
            self._items(self.plain_user).search_count([])
        with self.assertRaises(AccessError):
            self._items(self.plain_user).create({'parameter_id': param.id})

    def test_admin_can_read(self):
        """Requirements: FR-TCM-016, SC-TCM-016-2: a system administrator sees the list."""
        param = self._param('seen.acc', '1')
        item = self._items().create({'parameter_id': param.id})
        self.assertIn(item.id, self._items().search([]).ids)
