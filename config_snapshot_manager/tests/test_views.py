import base64
import json

from lxml import etree

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
