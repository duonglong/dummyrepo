from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

from odoo.addons.form_view_field_organizer.engine import expressions


class TestExpressions(TransactionCase):
    """Unit tests of the condition expression builder.

    Requirements: FR-FVF-0005, BR-FVF-0001
    """

    def test_presence_operators_on_any_type(self):
        """FR-FVF-0005: is set / is not set are offered for dates and x2many too."""
        for kind in ('date', 'many2many'):
            build = expressions.build_condition
            self.assertEqual(build([{'field': 'f', 'operator': 'set', 'value': ''}], {'f': kind}), 'f')
            self.assertEqual(build([{'field': 'f', 'operator': 'not_set', 'value': ''}], {'f': kind}), 'not f')

    def test_is_not_boolean_and_float(self):
        """FR-FVF-0005: values are parsed per type before they become literals."""
        build = expressions.build_condition
        self.assertEqual(build([{'field': 'b', 'operator': 'is', 'value': '1'}], {'b': 'boolean'}), 'b == True')
        self.assertEqual(build([{'field': 'x', 'operator': 'is_not', 'value': '2.5'}], {'x': 'float'}), 'x != 2.5')

    def test_text_value_cannot_inject_code(self):
        """FR-FVF-0005: typed text is quoted, never executed."""
        expr = expressions.build_condition(
            [{'field': 'n', 'operator': 'is', 'value': "x' or True or '"}], {'n': 'char'})
        self.assertEqual(expr, 'n == ' + repr("x' or True or '"))

    def test_bad_number_is_refused(self):
        """FR-FVF-0005: a value that does not parse as the field's type is refused."""
        with self.assertRaises(UserError):
            expressions.build_condition([{'field': 'q', 'operator': 'gt', 'value': 'abc'}], {'q': 'integer'})

    def test_combine_without_original_rule(self):
        """BR-FVF-0001: with no original invisible rule the field is hidden unless the condition holds."""
        self.assertEqual(expressions.combine(None, 'a'), 'not (a)')

    def test_non_finite_number_is_refused(self):
        """FR-FVF-0005: nan and inf are names, not literals; the browser would fail on them."""
        for kind in ('float', 'monetary'):
            for text in ('nan', 'inf', '-infinity', '1e999'):
                with self.assertRaises(UserError):
                    expressions.build_condition([{'field': 'q', 'operator': 'gt', 'value': text}], {'q': kind})

    def test_unknown_boolean_spelling_is_refused(self):
        """FR-FVF-0005: a yes/no value in an unknown spelling is refused, not read as no."""
        with self.assertRaises(UserError):
            expressions.build_condition([{'field': 'b', 'operator': 'is', 'value': 'yes'}], {'b': 'boolean'})
        self.assertEqual(
            expressions.build_condition([{'field': 'b', 'operator': 'is', 'value': 'false'}], {'b': 'boolean'}),
            'b == False')
