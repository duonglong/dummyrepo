import xml.etree.ElementTree as ET

from odoo.tests.common import TransactionCase

from ..engine import svg


def _node(node_id, level, field, kind='computed'):
    return {'id': node_id, 'level': level, 'model': 'm', 'field': field, 'label': field, 'kind': kind}


class TestSvg(TransactionCase):
    """Drawing of a payload.

    Requirements: FR-SFD-0006, FR-SFD-0008, FR-SFD-0009
    """

    def test_empty_map_has_no_drawing(self):
        """No boxes, no SVG."""
        self.assertEqual(svg.render_svg({'nodes': [], 'links': [], 'marks': []}, self.env._), '')

    def test_mark_labels_in_text(self):
        """Each mark type is written in the box, with the singular for one dependent."""
        payload = {
            'nodes': [_node(0, 0, 'a'), _node(1, 1, 'b', 'related'), _node(2, 2, 'c'), _node(3, 2, 'd')],
            'links': [{'from': 0, 'to': 1}, {'from': 1, 'to': 2}, {'from': 1, 'to': 3}, {'from': 3, 'to': 1}],
            'marks': [
                {'node': 2, 'type': 'too_deep', 'not_shown': 1},
                {'node': 3, 'type': 'too_large', 'not_shown': 4},
                {'node': 0, 'type': 'recursive', 'to': 0},
            ],
        }
        drawing = svg.render_svg(payload, self.env._)
        ET.fromstring(drawing)
        self.assertIn('too deep: 1 dependent not shown', drawing)
        self.assertIn('map too large: 4 dependents not shown', drawing)
        self.assertIn('recursive by design', drawing)
        self.assertIn('stroke-dasharray', drawing)

    def test_labels_are_escaped(self):
        """Markup in a label cannot reach the drawing."""
        payload = {'nodes': [dict(_node(0, 0, 'a'), label='<b>&x')], 'links': [], 'marks': []}
        drawing = svg.render_svg(payload, self.env._)
        ET.fromstring(drawing)
        self.assertIn('&lt;b&gt;&amp;x', drawing)
