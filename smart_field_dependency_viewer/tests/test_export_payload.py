from .sfd_fixture import SfdFixture


class TestExportPayload(SfdFixture):
    """The start box of the map says as much as every other box.

    Requirements: FR-SFD-0012
    """

    def test_start_node_of_a_plain_field(self):
        """FR-SFD-0012: the start box holds label, kind, method and related path."""
        start = self.map('x_sfd_a', 'x_quantity')['nodes'][0]
        self.assertEqual(start['kind'], 'plain')
        self.assertEqual(start['label'], 'x_quantity')
        self.assertIsNone(start['method'])
        self.assertIsNone(start['related'])

    def test_start_node_of_a_computed_field(self):
        """FR-SFD-0012: a computed start box holds its kind and compute method."""
        start = self.map('x_sfd_a', 'x_lx')['nodes'][0]
        self.assertEqual(start['kind'], 'computed')
        self.assertTrue(start['method'])
        self.assertIsNone(start['related'])
