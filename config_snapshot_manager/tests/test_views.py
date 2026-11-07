import base64
import json

from lxml import etree

from odoo.tools import file_open

from .common import ConfigSnapshotCase


class ViewCase(ConfigSnapshotCase):
    """Reads the arch the web client receives for a view of the module."""

    def _arch(self, model, view_type):
        arch = self.env[model].get_view(view_type=view_type)['arch']
        return etree.fromstring(arch)

    def _field(self, root, name):
        nodes = root.xpath(f'//field[@name="{name}"]')
        self.assertTrue(nodes, f'field {name} is not in the view')
        return nodes[0]


class TestCoveredPicker(ViewCase):
    """Tests for FR-TCM-001."""

    def test_bug_b_0103_picker_offers_no_create(self):
        """Regression test for B-0103: the picker must not offer Create.

        Requirements: FR-TCM-001
        """
        for view_type in ('list', 'form'):
            field = self._field(
                self._arch('config.snapshot.item', view_type), 'parameter_id')
            self.assertIn(
                'no_create', field.get('options', ''), f'{view_type} view')


class TestDifferencesDialog(ViewCase):
    """Tests for FR-TCM-006, FR-TCM-008, NFR-USE-001."""

    COUNTS = ('added_count', 'modified_count', 'removed_count')

    def _lines_list(self):
        root = self._arch('config.snapshot.import', 'form')
        return self._field(root, 'line_ids').xpath('.//list')[0]

    def test_bug_b_0104_counts_are_one_inline_row(self):
        """Regression test for B-0104: counts are not stacked labelled fields.

        Requirements: NFR-USE-001
        """
        root = self._arch('config.snapshot.import', 'form')
        for name in self.COUNTS:
            field = self._field(root, name)
            self.assertFalse(
                field.xpath('ancestor::group'),
                f'{name} sits in a group: it renders as a stacked labelled row')

    def test_bug_b_0104_headers_are_short_and_values_roomy(self):
        """Regression test for B-0104: no truncated headers, values not squeezed.

        Requirements: NFR-USE-001
        """
        lines = self._lines_list()
        for name in ('change_type', 'current_value', 'file_value'):
            header = lines.xpath(f'field[@name="{name}"]')[0].get('string')
            self.assertTrue(header and len(header) <= 8, f'{name}: {header!r}')
        for name in ('current_value', 'file_value'):
            self.assertIn(
                'text-break', lines.xpath(f'field[@name="{name}"]')[0].get('class', ''))

    def test_bug_b_0104_dialog_is_named_differences(self):
        """Regression test for B-0104: the review dialog is not titled 'Odoo'.

        Requirements: FR-TCM-006
        """
        self.env['config.snapshot.item'].search([]).unlink()
        wizard = self.env['config.snapshot.import'].create({
            'file': self._file_b64(), 'file_name': 'x.json'})
        action = wizard.action_compare()
        self.assertEqual(action.get('name'), 'Differences')

    def test_bug_b_0106_key_width_is_released_on_a_phone(self):
        """Regression test for B-0106: a fixed wide key column pushes values off a phone.

        The key keeps its desktop width (B-0077); a phone rule must release it.

        Requirements: NFR-USE-001
        """
        self.assertIn('cs_diff_list', self._lines_list().get('class', ''))
        scss = file_open('config_snapshot_manager/static/src/scss/wrap.scss').read()
        self.assertRegex(
            scss, r'@media \(max-width[^)]*\)\s*\{[^}]*cs_diff_list[^}]*data-name="key"[^}]*width:\s*auto')

    def _file_b64(self):
        payload = {'format': 'config_snapshot_manager', 'format_version': 1,
                   'settings': [{'key': 'b0104.new', 'value': 'v', 'sensitive': False}]}
        return base64.b64encode(json.dumps(payload).encode())
