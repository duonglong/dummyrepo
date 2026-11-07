"""B4: record rules whose domain turns into joins or sub-queries.

Requirements: FR-OPA-0019
"""
import ast
import logging

from .base import Check, register

_logger = logging.getLogger(__name__)

RELATIONS_LIMIT = 2  # a path through this many relations or more is complex
DOMAIN_CONNECTORS = ('&', '|', '!')
HIERARCHY_OPERATORS = ('child_of', 'parent_of')


def _leaf(node):
    """(path, operator) of a domain condition node, or None when the node is not a condition."""
    if not isinstance(node, (ast.Tuple, ast.List)) or len(node.elts) != 3:
        return None
    path, operator = node.elts[0], node.elts[1]
    if not all(isinstance(part, ast.Constant) and isinstance(part.value, str) for part in (path, operator)):
        return None
    return path.value, operator.value


def _scan(items, position, matches):
    """Read one operand of a prefix-notation domain from `position`.

    Returns the position after it and the set of first fields it uses; the complex
    parts found are appended to `matches`.
    """
    item = items[position]
    if isinstance(item, ast.Constant) and item.value in DOMAIN_CONNECTORS:
        arity = 1 if item.value == '!' else 2
        position += 1
        first_fields = []
        for _operand in range(arity):
            if position >= len(items):
                break
            position, operand_fields = _scan(items, position, matches)
            first_fields.append(operand_fields)
        combined = set().union(*first_fields)
        if item.value == '|' and len(combined) > 1:
            matches.append(f"or between {', '.join(sorted(combined))}")
        return position, combined
    leaf = _leaf(item)
    if leaf is None:
        return position + 1, set()
    path, operator = leaf
    if operator in HIERARCHY_OPERATORS:
        matches.append(f'{operator} on {path}')
    if path.count('.') >= RELATIONS_LIMIT:
        matches.append(path)
    return position + 1, {path.split('.')[0]}


def complex_parts(domain_text):
    """The parts of a record rule domain that make it complex, as readable texts (empty when it is not).

    Requirements: FR-OPA-0019
    """
    tree = ast.parse((domain_text or '').strip() or '[]', mode='eval').body
    if not isinstance(tree, ast.List):
        return []
    matches = []
    position = 0
    while position < len(tree.elts):
        position, _fields = _scan(tree.elts, position, matches)
    return matches


@register
class ComplexRulesCheck(Check):
    """Active record rules with long paths, hierarchy operators or an or across fields."""

    code = 'B4'

    def run(self, ctx):
        """Report each active record rule whose domain is complex.

        Requirements: FR-OPA-0019, BR-OPA-0001
        """
        rows_by_table = ctx.probe.estimated_rows()
        # the audit reads every rule, whatever the rights of the user who started it
        rules = ctx.env['ir.access'].sudo().search([('active', '=', True), ('domain', '!=', False)])
        findings = []
        for rule in rules:
            try:
                parts = complex_parts(rule.domain)
            except SyntaxError:
                _logger.warning('Record rule %s has a domain that cannot be read: %s', rule.id, rule.domain)
                continue
            if parts:
                findings.append(self._finding(ctx, rule, parts, rows_by_table))
        return findings

    def _finding(self, ctx, rule, parts, rows_by_table):
        """The finding of one complex record rule."""
        model_name = rule.model_id.model
        table = ctx.env[model_name]._table if model_name in ctx.env.registry else None
        return {
            'identity_key': f'B4:{rule.id}',
            'message': ctx.env._(
                'Record rule %(rule)s on %(model)s is complex: %(parts)s.',
                rule=rule.name, model=model_name, parts='; '.join(parts)),
            'model_id': rule.model_id.id,
            'rule_id': rule.id,
            'table_name': table,
            'row_estimate': rows_by_table.get(table),
            'evidence': {'domain': rule.domain, 'matched': parts, 'rows_estimate': rows_by_table.get(table)},
        }
