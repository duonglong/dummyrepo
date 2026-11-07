from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from ..const import MASK
from .common import ConfigSnapshotCase


@tagged('post_install', '-at_install')
class TestItem(ConfigSnapshotCase):
    """Requirements: FR-TCM-001, FR-TCM-002, FR-TCM-016, BR-TCM-001, BR-TCM-003"""

    def _items(self, user=None):
        return self.env['config.snapshot.item'].with_user(user or self.admin_user)

    def test_add_covered(self):
        param = self._param('web.base.url.t', 'https://a.example')
        self.assertEqual(self._items().create({'parameter_id': param.id}).key, 'web.base.url.t')

    def test_remove_keeps_param(self):
        param = self._param('keep.t', 'https://a.example')
        self._items().create({'parameter_id': param.id}).unlink()
        self.assertEqual(param.value, 'https://a.example')

    def test_duplicate_refused(self):
        param = self._param('dup.t', '1')
        self._items().create({'parameter_id': param.id})
        with self.assertRaises((IntegrityError, ValidationError)), \
                mute_logger('odoo.sql_db'), self.cr.savepoint():
            self._items().create({'parameter_id': param.id})

    def test_sensitive_flag(self):
        item = self._items().create({'parameter_id': self._param('flag.t', '1').id})
        self.assertFalse(item.sensitive)
        item.write({'sensitive': True})
        self.assertTrue(item.sensitive)

    def test_value_display(self):
        item = self._items().create({'parameter_id': self._param('s.t', 's3cret').id, 'sensitive': True})
        self.assertEqual(item.value_display, MASK)
        item.sensitive = False
        self.assertEqual(item.value_display, 's3cret')

    def test_non_admin_refused(self):
        param = self._param('acc.t', '1')
        with self.assertRaises(AccessError):
            self._items(self.plain_user).search([])
        with self.assertRaises(AccessError):
            self._items(self.plain_user).create({'parameter_id': param.id})
        self.assertFalse(self._items().search([('key', '=', 'zzz')]))
