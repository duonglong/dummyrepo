from unittest.mock import patch

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
        """A native computed dependent of res.partner.parent_id is listed with its declared path and method."""
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

    def test_shared_prefix_joins_every_declared_path(self):
        """Two declared paths through one relation are both shown on its row."""
        paths = ['line_ids.price', 'line_ids.quantity', 'other']
        self.assertEqual(graph._declared_paths(paths, ['line_ids']), ['line_ids.price', 'line_ids.quantity'])

    def test_broken_manual_field_is_skipped(self):
        """A manual field whose dependency is missing is skipped; the other dependents are kept."""
        registry = self.env.registry
        broken = registry['res.partner']._fields['commercial_partner_id']
        original = type(broken).resolve_depends

        def resolve(field, reg):
            if field is broken:
                raise ValueError('missing dependency')
            return original(field, reg)

        with patch.object(type(broken), 'resolve_depends', resolve), \
                patch.object(type(broken.base_field), 'manual', True, create=True):
            with self.assertLogs('odoo.addons.smart_field_dependency_viewer.engine.graph', 'WARNING'):
                index = graph.build_reverse_index(registry)
        self.assertTrue(index)

    def test_broken_standard_field_re_raises(self):
        """A non-manual field with a missing dependency is not hidden."""
        registry = self.env.registry
        broken = registry['res.partner']._fields['commercial_partner_id']

        def resolve(field, reg):
            raise ValueError('missing dependency')

        with patch.object(type(broken), 'resolve_depends', resolve), \
                self.assertRaises(ValueError):
            graph.build_reverse_index(registry)
