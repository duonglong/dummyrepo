import time
import xml.etree.ElementTree as ET

from .sfd_fixture import SfdFixture


class TestChainAcceptance(SfdFixture):
    """Acceptance tests of T-004: levels, loop and too-deep marks, the SVG, speed.

    Requirements: BR-SFD-0002, BR-SFD-0003, FR-SFD-0006, FR-SFD-0008,
    FR-SFD-0009, NFR-PERF-0001.
    Chains are custom fields of x_sfd_a: x_c0 <- x_c1 <- ... <- x_c4 (a chain
    four levels long below x_c0), a diamond x_q -> x_s, x_d -> x_t, a loop
    x_lq -> x_lx <-> x_ly. The map is read from get_dependency_map; the
    drawing in the widget is left to the build.
    """

    def _levels(self, payload):
        return {n['field']: n['level'] for n in payload['nodes']}

    def _node(self, payload, field):
        found = self.nodes_of(payload, field)
        self.assertTrue(found, "no node for %s" % field)
        return found[0]

    def test_levels_of_a_chain(self):
        """SC-SFD-0006-1 (FR-SFD-0006, BR-SFD-0002): start is level 0, each dependent one level more, with arrows."""
        self.set_limit(self.anna, 5)
        payload = self.map('x_sfd_a', 'x_c1')
        self.assertEqual(self._levels(payload), {'x_c1': 0, 'x_c2': 1, 'x_c3': 2, 'x_c4': 3})
        for src, dst in (('x_c1', 'x_c2'), ('x_c2', 'x_c3'), ('x_c3', 'x_c4')):
            ids = (self._node(payload, src)['id'], self._node(payload, dst)['id'])
            self.assertIn(ids, [(link['from'], link['to']) for link in payload['links']])

    def test_field_on_two_branches_drawn_on_each(self):
        """SC-SFD-0006-2 (FR-SFD-0006, BR-SFD-0003): a field reached by two branches appears twice, not circular."""
        payload = self.map('x_sfd_a', 'x_q')
        self.assertEqual(len(self.nodes_of(payload, 'x_t')), 2)
        self.assertEqual(payload['marks'], [])
        parents = {link['from'] for link in payload['links']
                   if link['to'] in {n['id'] for n in self.nodes_of(payload, 'x_t')}}
        self.assertEqual(parents, {self._node(payload, 'x_s')['id'], self._node(payload, 'x_d')['id']})

    def test_accidental_loop_marked_circular(self):
        """SC-SFD-0008-1 (FR-SFD-0008, BR-SFD-0003): the link y back to x is circular and not expanded."""
        payload = self.map('x_sfd_a', 'x_lq')
        x, y = self._node(payload, 'x_lx'), self._node(payload, 'x_ly')
        marks = self.node_marks(payload, y['id'], 'circular')
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]['to'], x['id'])
        self.assertIn((y['id'], x['id']), [(link['from'], link['to']) for link in payload['links']])
        self.assertEqual(len(self.nodes_of(payload, 'x_lx')), 1)
        self.assertEqual(len(self.nodes_of(payload, 'x_ly')), 1)
        self.assertFalse([m for m in payload['marks'] if m['type'] == 'recursive'])

    def test_recursive_by_design_labelled_differently(self):
        """SC-SFD-0008-2 (FR-SFD-0008): the link back to commercial_partner_id is 'recursive', not 'circular'."""
        payload = self.map('res.partner', 'parent_id')
        commercial = self._node(payload, 'commercial_partner_id')
        marks = [m for m in payload['marks'] if m['type'] == 'recursive' and m['to'] == commercial['id']]
        self.assertTrue(marks)
        self.assertFalse([m for m in payload['marks']
                          if m['type'] == 'circular' and m['to'] == commercial['id']])

    def test_chain_one_level_past_limit_too_deep(self):
        """SC-SFD-0009-1 (FR-SFD-0009): limit 3 on a 4-level chain draws levels 0-3 and marks level 3."""
        self.set_limit(self.anna, 3)
        payload = self.map('x_sfd_a', 'x_c0')
        self.assertEqual(self._levels(payload), {'x_c0': 0, 'x_c1': 1, 'x_c2': 2, 'x_c3': 3})
        marks = self.node_marks(payload, self._node(payload, 'x_c3')['id'], 'too_deep')
        self.assertEqual([m['not_shown'] for m in marks], [1])
        self.assertIn('1 dependent not shown', payload['svg'])

    def test_chain_exactly_at_limit_not_flagged(self):
        """SC-SFD-0009-2 (BR-SFD-0002): a chain three levels long with limit 3 has no too-deep mark."""
        self.set_limit(self.anna, 3)
        payload = self.map('x_sfd_a', 'x_c1')
        self.assertEqual(max(n['level'] for n in payload['nodes']), 3)
        self.assertFalse([m for m in payload['marks'] if m['type'] == 'too_deep'])

    def test_limit_boundaries(self):
        """FR-SFD-0009 boundary rows: limit 4 on the 4-level chain is not flagged, limit 2 flags level 2."""
        self.set_limit(self.anna, 4)
        payload = self.map('x_sfd_a', 'x_c0')
        self.assertFalse([m for m in payload['marks'] if m['type'] == 'too_deep'])
        self.assertEqual(max(n['level'] for n in payload['nodes']), 4)
        self.set_limit(self.anna, 2)
        payload = self.map('x_sfd_a', 'x_c0')
        self.assertEqual(max(n['level'] for n in payload['nodes']), 2)
        self.assertTrue(self.node_marks(payload, self._node(payload, 'x_c2')['id'], 'too_deep'))

    def test_svg_is_valid_and_labelled(self):
        """FR-SFD-0006, FR-SFD-0008: the SVG is well-formed XML, names every box and labels the marks in text."""
        payload = self.map('x_sfd_a', 'x_lq')
        root = ET.fromstring(payload['svg'])
        self.assertTrue(root.tag.endswith('svg'))
        self.assertIn('x_lx', payload['svg'])
        self.assertIn('x_ly', payload['svg'])
        self.assertIn('circular', payload['svg'])
        recursive = self.map('res.partner', 'parent_id')
        self.assertIn('recursive by design', recursive['svg'])

    def test_svg_escapes_labels(self):
        """Design risk table: markup in a field label is escaped in the SVG."""
        payload = self.map('x_sfd_a', 'x_markup')
        ET.fromstring(payload['svg'])
        self.assertNotIn('<b>&x', payload['svg'])
        self.assertIn('&lt;b&gt;&amp;x', payload['svg'])

    def test_map_shows_within_three_seconds(self):
        """NFR-PERF-0001: a wide chain at limit 5 shows within 3 seconds, on the first and the second call."""
        self.set_limit(self.anna, 5)
        for model, field in (('res.company', 'currency_id'), ('res.partner', 'company_id')):
            for _attempt in range(2):
                start = time.perf_counter()
                payload = self.map(model, field)
                elapsed = time.perf_counter() - start
                self.assertLess(elapsed, 3.0, "%s.%s took %.2fs" % (model, field, elapsed))
                self.assertLessEqual(len(payload['nodes']), 1000)
