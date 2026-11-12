import json

from .sfd_fixture import SfdFixture


class TestExportPayloadAcceptance(SfdFixture):
    """Acceptance tests of T-005: what the exports read from the map.

    Requirements: FR-SFD-0011, FR-SFD-0012.
    The PNG and the JSON are made and downloaded in the browser from the map
    returned by get_dependency_map (the file names, the click and the drawing
    are left to the build and its browser test). Here the map is checked to
    hold, for every shown field, everything the data file must carry, and to
    be plain JSON, so the two exports never disagree. The view has no
    server-side export action, so no Form step applies.
    """

    def _assert_complete(self, payload):
        for node in payload['nodes']:
            for key in ('model', 'field', 'kind', 'level', 'method', 'related'):
                self.assertIn(key, node, "node %s lacks %s" % (node.get('field'), key))
            self.assertIn(node['kind'], ('plain', 'computed', 'related'))
        for link in payload['links']:
            self.assertIn('path', link)
            self.assertTrue(link['path'])

    def test_data_file_content_of_a_map(self):
        """SC-SFD-0012-1 (FR-SFD-0012): start, limit, fields with model/kind/level/method or path, links with path."""
        self.set_limit(self.anna, 5)
        payload = self.map('x_sfd_b', 'x_partner_id')
        self.assertEqual(payload['start']['model'], 'x_sfd_b')
        self.assertEqual(payload['start']['field'], 'x_partner_id')
        self.assertEqual(payload['depth_limit'], 5)
        self._assert_complete(payload)
        shown = {(n['model'], n['field']): n for n in payload['nodes']}
        self.assertEqual(shown[('x_sfd_b', 'x_partner_id')]['level'], 0)
        related = shown[('x_sfd_b', 'x_partner_country_id')]
        self.assertEqual(related['kind'], 'related')
        self.assertEqual(related['level'], 1)
        self.assertEqual(related['related'], 'x_partner_id.country_id')
        self.assertTrue(payload['links'])

    def test_computed_field_carries_its_method_or_path(self):
        """SC-SFD-0012-1 (FR-SFD-0012): a computed field shown carries its kind and declared path."""
        payload = self.map('x_sfd_a', 'x_quantity')
        self._assert_complete(payload)
        subtotal = self.nodes_of(payload, 'x_subtotal')[0]
        self.assertEqual(subtotal['kind'], 'computed')
        self.assertEqual(subtotal['level'], 1)
        self.assertIn('x_quantity', payload['links'][0]['path'])

    def test_marks_are_in_the_data(self):
        """SC-SFD-0011-1, SC-SFD-0012-1: the circular mark shown in the diagram is in the data too."""
        payload = self.map('x_sfd_a', 'x_lq')
        self._assert_complete(payload)
        self.assertEqual([m['type'] for m in payload['marks']], ['circular'])
        self.assertIn('circular', payload['svg'].lower())

    def test_too_deep_mark_is_in_the_data(self):
        """SC-SFD-0012-1 (FR-SFD-0012): the too-deep mark is part of the data."""
        self.set_limit(self.anna, 3)
        payload = self.map('x_sfd_a', 'x_c0')
        self.assertEqual([m['type'] for m in payload['marks']], ['too_deep'])

    def test_map_is_plain_json(self):
        """SC-SFD-0012-1 (FR-SFD-0012): the map survives a JSON round trip unchanged."""
        payload = self.map('x_sfd_a', 'x_lq')
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_image_source_is_a_drawing_with_marks(self):
        """SC-SFD-0011-1 (FR-SFD-0011): the map holds the SVG the PNG is made from."""
        payload = self.map('x_sfd_a', 'x_lq')
        self.assertTrue(payload['svg'].lstrip().startswith('<svg'))
        self.assertIn('x_lx', payload['svg'])

    def test_empty_map_has_nothing_to_export(self):
        """SC-SFD-0007-1 (FR-SFD-0007): no dependents, so the map is flagged empty and holds no drawing."""
        payload = self.map('x_sfd_a', 'x_reference')
        self.assertTrue(payload['empty'])
        self.assertFalse(payload['svg'])
