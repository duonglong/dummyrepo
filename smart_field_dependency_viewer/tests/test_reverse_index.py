from odoo.tests.common import TransactionCase

from ..engine import graph


def _row(model, field, path='x'):
    return {'model': model, 'field': field, 'label': field, 'kind': 'computed',
            'stored': False, 'path': path, 'method': None, 'related': None}


class TestReverseIndex(TransactionCase):
    """Reverse index and walk on native fields and a hand-built index.

    Requirements: BR-SFD-0001, FR-SFD-0002, FR-SFD-0003, FR-SFD-0004
    """

    def test_native_related_and_computed_dependents(self):
        """res.partner.country_id feeds related and computed fields, with their declared path."""
        index = graph.build_reverse_index(self.env.registry)
        rows = {(r['model'], r['field']): r for r in index[('res.partner', 'parent_id')]}
        row = rows[('res.partner', 'commercial_partner_id')]
        self.assertEqual(row['kind'], 'computed')
        self.assertEqual(row['method'], '_compute_commercial_partner')
        self.assertIn('parent_id.commercial_partner_id', row['path'])

    def test_plain_field_is_not_a_dependent(self):
        """A plain field never appears as a dependent of anything."""
        index = graph.build_reverse_index(self.env.registry)
        dependents = {(r['model'], r['field']) for rows in index.values() for r in rows}
        self.assertNotIn(('res.partner', 'name'), dependents)

    def test_own_dependencies_none_for_plain_field(self):
        """own_dependencies is None for a plain field and the declared paths for a computed one."""
        registry = self.env.registry
        self.assertIsNone(graph.own_dependencies(registry, 'res.partner', 'name'))
        self.assertIn('parent_id.commercial_partner_id',
                      graph.own_dependencies(registry, 'res.partner', 'commercial_partner_id'))

    def test_walk_stops_at_limit(self):
        """The walk adds levels up to the limit and no deeper."""
        index = {('m', 'a'): [_row('m', 'b')], ('m', 'b'): [_row('m', 'c')], ('m', 'c'): [_row('m', 'd')]}
        result = graph.walk(index, ('m', 'a'), 2)
        self.assertEqual([n['field'] for n in result['nodes']], ['a', 'b', 'c'])
        self.assertEqual(len(result['links']), 2)

    def test_walk_does_not_loop(self):
        """A dependent already on the path is linked but not expanded again."""
        index = {('m', 'a'): [_row('m', 'b')], ('m', 'b'): [_row('m', 'a')]}
        result = graph.walk(index, ('m', 'a'), 10)
        self.assertEqual([n['field'] for n in result['nodes']], ['a', 'b', 'a'])
