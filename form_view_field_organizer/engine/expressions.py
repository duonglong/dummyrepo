"""Condition expressions of the layout engine: pure functions, no ORM."""
import math

from odoo import _
from odoo.exceptions import UserError

PRESENCE_OPERATORS = ('set', 'not_set')
EQUALITY_OPERATORS = {'is': '==', 'is_not': '!='}
ORDER_OPERATORS = {'gt': '>', 'lt': '<', 'ge': '>=', 'le': '<='}

EQUALITY_FIELD_TYPES = (
    'char', 'text', 'selection', 'boolean', 'integer', 'float', 'monetary', 'many2one')
ORDER_FIELD_TYPES = ('integer', 'float', 'monetary')

BOOLEAN_TEXTS = {'1': True, 'true': True, 'True': True, '0': False, 'false': False, 'False': False}


def _parse_boolean(text):
    return BOOLEAN_TEXTS[text]


def _parse_finite_float(text):
    number = float(text)
    if not math.isfinite(number):
        raise ValueError(text)
    return number


LITERAL_PARSERS = {
    'integer': int,
    'many2one': int,
    'float': _parse_finite_float,
    'monetary': _parse_finite_float,
    'boolean': _parse_boolean,
}


def _literal(field_type, text):
    """Return the Python literal of ``text`` parsed as ``field_type``."""
    parse = LITERAL_PARSERS.get(field_type, str)
    try:
        return repr(parse(text))
    except (ValueError, KeyError, TypeError):
        raise UserError(_("Enter a value for this comparison."))


def _check_operator(operator, field_type):
    """Refuse an operator the field's kind does not offer."""
    if operator in PRESENCE_OPERATORS:
        return
    allowed = (
        (operator in EQUALITY_OPERATORS and field_type in EQUALITY_FIELD_TYPES)
        or (operator in ORDER_OPERATORS and field_type in ORDER_FIELD_TYPES))
    if not allowed:
        raise UserError(_("This comparison cannot be used with this kind of field."))


def build_condition_line(line, field_types):
    """Return the Python expression of one condition line.

    Requirements: FR-FVF-0005
    """
    field, operator = line['field'], line['operator']
    _check_operator(operator, field_types.get(field))
    if operator == 'set':
        return field
    if operator == 'not_set':
        return 'not %s' % field
    sign = EQUALITY_OPERATORS.get(operator) or ORDER_OPERATORS[operator]
    return '%s %s %s' % (field, sign, _literal(field_types[field], line['value']))


def build_condition(lines, field_types):
    """Join the condition lines with ``and`` into one expression.

    Values become Python literals through ``repr`` of the parsed typed value,
    never typed text.

    Requirements: FR-FVF-0005
    """
    return ' and '.join(build_condition_line(line, field_types) for line in lines)


def combine(original_invisible, condition):
    """Return the invisible rule showing the field only while ``condition`` holds.

    The original rule is kept with ``or``, so a layout never reveals a field
    the original form hides.

    Requirements: FR-FVF-0005, BR-FVF-0001
    """
    if not original_invisible:
        return 'not (%s)' % condition
    return '(%s) or not (%s)' % (original_invisible, condition)
