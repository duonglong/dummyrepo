from unittest.mock import patch

from odoo.tests.common import TransactionCase

from ..engine import graph


def _row(field, model='m'):
    return {'model': model, 'field': field, 'label': field, 'kind': 'computed',
            'stored': False, 'path': 'p', 'method': None, 'related': None}


def _marks(result, kind):
    return [m for m in result['marks'] if m['type'] == kind]


class TestWalk(TransactionCase):
    """Marks of the walk on a hand-built index.

    Requirements: BR-SFD-0002, BR-SFD-0003, FR-SFD-0008, FR-SFD-0009
    """

    def test_too_deep_counts_distinct_dependents_at_the_limit(self):
        """A field at the limit with two dependents gets one too_deep mark counting 2."""
        index = {('m', 'a'): [_row('b')], ('m', 'b'): [_row('c'), _row('d')]}
        result = graph.walk(index, ('m', 'a'), 1)
        self.assertEqual([n['field'] for n in result['nodes']], ['a', 'b'])
        self.assertEqual([(m['node'], m['not_shown']) for m in _marks(result, 'too_deep')], [(1, 2)])

    def test_field_at_the_limit_without_dependents_is_not_flagged(self):
        """Boundary: the last field at the limit has no dependents, so no mark."""
        index = {('m', 'a'): [_row('b')]}
        self.assertEqual(graph.walk(index, ('m', 'a'), 1)['marks'], [])

    def test_loop_to_self_is_recursive_when_declared(self):
        """A field that depends on itself is 'recursive' when declared so, else 'circular'."""
        index = {('m', 'a'): [_row('b')], ('m', 'b'): [_row('b')]}
        self.assertEqual(len(_marks(graph.walk(index, ('m', 'a'), 5, lambda key: True), 'recursive')), 1)
        self.assertEqual(len(_marks(graph.walk(index, ('m', 'a'), 5), 'circular')), 1)

    def test_loop_back_to_start_is_circular(self):
        """A dependent that leads back to the starting field is a loop onto level 0."""
        index = {('m', 'a'): [_row('b')], ('m', 'b'): [_row('a')]}
        result = graph.walk(index, ('m', 'a'), 5)
        self.assertEqual([(m['node'], m['to']) for m in _marks(result, 'circular')], [(1, 0)])

    def test_node_budget_marks_unexpanded_branches(self):
        """Past the budget each branch left out is marked too_large and the walk says truncated."""
        index = {('m', 'a'): [_row('b'), _row('c'), _row('d')]}
        with patch.object(graph, 'MAX_NODES', 3):
            result = graph.walk(index, ('m', 'a'), 5)
        self.assertEqual(len(result['nodes']), 3)
        self.assertTrue(result['truncated'])
        self.assertEqual([(m['node'], m['not_shown']) for m in _marks(result, 'too_large')], [(0, 1)])
