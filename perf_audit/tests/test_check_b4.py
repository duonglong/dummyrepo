from odoo.tests.common import TransactionCase, tagged

from ..checks.b4_rules import complex_parts


@tagged('post_install', '-at_install')
class TestComplexParts(TransactionCase):
    """Requirements: FR-OPA-0019"""

    def test_long_path(self):
        """FR-OPA-0019: two relations is complex, one relation is not."""
        self.assertEqual(complex_parts("[('a.b.c', '=', 1)]"), ['a.b.c'])
        self.assertEqual(complex_parts("[('a.b', '=', 1)]"), [])

    def test_or_across_first_fields(self):
        """FR-OPA-0019: or of two first fields is reported, or of one is not."""
        self.assertEqual(len(complex_parts("['|', ('a', '=', 1), ('b.x', '=', 2)]")), 1)
        self.assertEqual(complex_parts("['|', ('a', '=', 1), ('a.x', '=', 2)]"), [])

    def test_nested_or_inside_and(self):
        """FR-OPA-0019: an or after an and still reads its own two operands."""
        domain = "['&', ('c', '=', 1), '|', ('a', '=', 1), ('b', '=', 2)]"
        self.assertEqual(len(complex_parts(domain)), 1)

    def test_hierarchy_operator(self):
        """FR-OPA-0019: child_of and parent_of are reported."""
        self.assertEqual(len(complex_parts("[('a', 'parent_of', 1)]")), 1)

    def test_empty_or_not_a_list(self):
        """FR-OPA-0019: an empty domain or an expression that is no list literal is not reported."""
        self.assertEqual(complex_parts(''), [])
        self.assertEqual(complex_parts("foo()"), [])
