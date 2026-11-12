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

    def test_box_with_two_marks_shows_both_labels(self):
        """An accidental loop beside a recursive one, and a cut map on a looping box, keep every label."""
        payload = {
            'nodes': [_node(0, 0, 'a'), _node(1, 1, 'b')],
            'links': [{'from': 0, 'to': 1}, {'from': 1, 'to': 0}, {'from': 1, 'to': 1}],
            'marks': [
                {'node': 1, 'type': 'recursive', 'to': 1},
                {'node': 1, 'type': 'circular', 'to': 0},
                {'node': 1, 'type': 'too_large', 'not_shown': 3},
            ],
        }
        drawing = svg.render_svg(payload, self.env._)
        ET.fromstring(drawing)
        self.assertIn('recursive by design', drawing)
        self.assertEqual(drawing.count('>circular<'), 1)
        self.assertIn('map too large: 3 dependents not shown', drawing)

    def test_box_border_takes_the_most_important_mark(self):
        """An accidental loop wins over a recursive mark on the same box border."""
        payload = {
            'nodes': [_node(0, 0, 'a'), _node(1, 1, 'b')],
            'links': [{'from': 0, 'to': 1}],
            'marks': [{'node': 1, 'type': 'recursive', 'to': 1}, {'node': 1, 'type': 'circular', 'to': 0}],
        }
        drawing = svg.render_svg(payload, self.env._)
        self.assertIn(f'stroke="{svg.MARK_COLOURS["circular"]}" stroke-width="2"', drawing)

    def test_self_loop_arrow_has_width(self):
        """A link from a box to itself curves out to the side instead of a flat line."""
        payload = {
            'nodes': [_node(0, 0, 'a')],
            'links': [{'from': 0, 'to': 0}],
            'marks': [{'node': 0, 'type': 'recursive', 'to': 0}],
        }
        drawing = svg.render_svg(payload, self.env._)
        path = ET.fromstring(drawing).find('.//{http://www.w3.org/2000/svg}path[@fill="none"]').get('d')
        start_x = path.split(' ')[0][1:].split(',')[0]
        end_x = path.split(' ')[-1].split(',')[0]
        self.assertNotEqual(start_x, end_x)
