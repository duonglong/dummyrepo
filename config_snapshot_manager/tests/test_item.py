import ast
import os
import re

from lxml import etree
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


@tagged('post_install', '-at_install')
class TestCoveredListUx(ConfigSnapshotCase):
    """Regression tests of the UX findings of the covered settings list."""

    def _list_arch(self):
        arch = self.env['config.snapshot.item'].get_view(view_type='list')['arch']
        return etree.fromstring(arch)

    def test_bug_b_0080_export_import_buttons_above_list(self):
        """Regression test for B-0080: Export and Import buttons are not above the covered list.

        Requirements: FR-TCM-003, FR-TCM-005
        """
        buttons = self._list_arch().xpath('//header/button[@display="always"]')
        expected = {
            self.env.ref('config_snapshot_manager.config_snapshot_export_action').id,
            self.env.ref('config_snapshot_manager.config_snapshot_import_action').id,
        }
        self.assertEqual({int(b.get('name')) for b in buttons}, expected)

    def test_bug_b_0081_setting_and_key_not_duplicated(self):
        """Regression test for B-0081: long names cut, Setting and Key columns repeat each other.

        Requirements: NFR-USE-001
        """
        arch = self._list_arch()
        visible = [f.get('name') for f in arch.xpath('//field')
                   if f.get('optional') != 'hide' and f.get('column_invisible') != '1']
        self.assertFalse({'parameter_id', 'key'} <= set(visible), 'Setting and Key show the same text')
        name_field = arch.xpath('//field[@name="parameter_id"] | //field[@name="key"]')
        widths = [int((f.get('width') or '0').replace('px', '')) for f in name_field
                  if f.get('name') in visible]
        self.assertTrue(widths and max(widths) >= 320, 'the key column must be wide enough to read')
        sensitive = arch.xpath('//field[@name="sensitive"]')[0]
        self.assertGreaterEqual(int((sensitive.get('width') or '0').replace('px', '')), 90)


@tagged('post_install', '-at_install')
class TestLongKeyWrap(ConfigSnapshotCase):
    """Requirements: NFR-USE-001"""

    def _classes(self, model, xmlid, xpath):
        arch = self.env[model].get_view(self.env.ref(xmlid).id)['arch']
        return (etree.fromstring(arch).xpath(xpath)[0].get('class') or '').split()

    def test_bug_b_0083_long_keys_wrap(self):
        """Regression test for B-0083: long keys are cut with an ellipsis.

        Requirements: NFR-USE-001
        """
        item = 'config_snapshot_manager.config_snapshot_item_view_list'
        imp = 'config_snapshot_manager.config_snapshot_import_view_form'
        for model, xmlid, xpath in (
            ('config.snapshot.item', item, "//field[@name='parameter_id']"),
            ('config.snapshot.item', item, "//field[@name='key']"),
            ('config.snapshot.import', imp, "//list//field[@name='key']"),
        ):
            self.assertIn('cs_wrap_cell', self._classes(model, xmlid, xpath), xpath)

    def test_bug_b_0083_stylesheet_declared(self):
        """Regression test for B-0083: the wrapping rule is loaded in the backend.

        Requirements: NFR-USE-001
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'static/src/scss/wrap.scss')) as f:
            css = f.read()
        self.assertIn('.cs_wrap_cell', css)
        self.assertIn('overflow-wrap', css)
        with open(os.path.join(root, '__manifest__.py')) as f:
            manifest = ast.literal_eval(f.read())
        self.assertIn('config_snapshot_manager/static/src/scss/wrap.scss',
                      manifest.get('assets', {}).get('web.assets_backend', []))

    def test_bug_b_0084_rule_beats_core_nowrap(self):
        """Regression test for B-0084: long keys were still cut in the demo.

        Core list rules (.o_data_row:not(.o_selected_row) .o_list_char,
        with white-space: nowrap, overflow hidden and an ellipsis on inner
        spans) are more specific than our selectors, so the wrapping rule
        must be !important to win.

        Requirements: NFR-USE-001
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'static/src/scss/wrap.scss')) as f:
            css = f.read()
        for decl in ('white-space: normal', 'text-overflow: clip',
                     'overflow: visible', 'max-width: none'):
            self.assertRegex(css, re.escape(decl) + r'\s*!important', decl)
