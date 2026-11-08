"""B3: stored fields of big tables used in domains and sort orders with no index.

Requirements: FR-OPA-0018
"""
import ast
import logging
from collections import defaultdict

from lxml import etree

from odoo.exceptions import UserError
from odoo.tools.sql import make_index_name

from .base import Check, register

_logger = logging.getLogger(__name__)


def domain_field_names(domain):
    """Names of the first path step of every leaf of a domain text, read without evaluating it.

    Requirements: FR-OPA-0018
    """
    try:
        tree = ast.parse((domain or '').strip(), mode='eval')
    except SyntaxError:
        return set()
    if not isinstance(tree.body, (ast.List, ast.Tuple)):
        return set()
    names = set()
    for leaf in tree.body.elts:  # only the leaves of this domain: not values, not 'any' sub-domains
        if isinstance(leaf, (ast.Tuple, ast.List)) and len(leaf.elts) == 3:
            name = leaf.elts[0]
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                names.add(name.value.split('.')[0])
    return names


def order_field_names(order):
    """Names of the fields of a `_order` text."""
    return [part.split()[0].split('.')[0] for part in (order or '').split(',') if part.strip()]


@register
class DomainIndexCheck(Check):
    """Report stored fields of big tables filtered or sorted on with no index starting on them.

    Requirements: FR-OPA-0018
    """

    code = 'B3'

    def run(self, ctx):
        """Return one finding per unindexed field, listing every use of it.

        Requirements: FR-OPA-0018
        """
        env = ctx.env
        rows = ctx.probe.estimated_rows()
        indexed = ctx.probe.indexed_leading_columns()
        big_table = ctx.thresholds['big_table']
        big_models = {
            name for name, Model in env.registry.items()
            if Model._auto and not Model._abstract and rows.get(Model._table, 0) > big_table}
        skipped_views = []
        uses = self._uses(env, big_models, skipped_views)
        findings = []
        for (model_name, field_name), references in sorted(uses.items()):
            Model = env[model_name]
            field = Model._fields.get(field_name)
            if not (field and field.store and field.column_type) or (Model._table, field_name) in indexed:
                continue
            findings.append(self._finding(ctx, Model, field, references, rows[Model._table], big_table))
        if skipped_views:
            ctx.outcome['part_skip_reason'] = env._('Views whose final arch could not be read: %s', ', '.join(map(str, skipped_views)))
        return findings

    def _uses(self, env, model_names, skipped_views):
        """Every use of a field in a domain or default order of these models: {(model, field): [use]}."""
        uses = defaultdict(list)
        sudo = env(su=True)
        for access in sudo['ir.access'].search([('model_id.model', 'in', list(model_names)), ('domain', '!=', False)]):
            self._add(uses, access.model_id.model, domain_field_names(access.domain),
                      {'type': 'record rule', 'id': access.id, 'name': access.name})
        for action in sudo['ir.actions.act_window'].search([('res_model', 'in', list(model_names)), ('domain', '!=', False)]):
            self._add(uses, action.res_model, domain_field_names(action.domain),
                      {'type': 'window action', 'id': action.id, 'name': action.name})
        for view in sudo['ir.ui.view'].search([('type', '=', 'search'), ('mode', '=', 'primary'), ('model', 'in', list(model_names))]):
            names = set()
            for domain in self._filter_domains(sudo[view.model], view, skipped_views):
                names |= domain_field_names(domain)
            self._add(uses, view.model, names, {'type': 'search filter', 'id': view.id, 'name': view.name})
        for name in model_names:
            self._add(uses, name, set(order_field_names(env[name]._order)),
                      {'type': 'default sort order', 'id': False, 'name': env[name]._order})
        return uses

    def _filter_domains(self, Model, view, skipped_views):
        """The domains of the filters of the final arch of a search view."""
        try:
            arch = Model.get_views([(view.id, 'search')])['views']['search']['arch']
        except (UserError, ValueError, KeyError):
            _logger.warning('B3 could not read the final arch of view %s', view.id, exc_info=True)
            skipped_views.append(view.id)
            return []
        return etree.fromstring(arch).xpath('.//filter/@domain')

    def _add(self, uses, model_name, field_names, use):
        for field_name in field_names:
            uses[(model_name, field_name)].append(use)

    def _finding(self, ctx, Model, field, references, rows, big_table):
        """The finding of one unindexed field, with an index as the fix."""
        index_name = make_index_name(Model._table, field.name)
        return {
            'identity_key': 'B3:%s.%s' % (Model._name, field.name),
            'message': 'Field %s.%s is used in %d place(s) and no index starts on it' % (
                Model._name, field.name, len(references)),
            'table_name': Model._table,
            'row_estimate': rows,
            'model_id': ctx.env['ir.model']._get_id(Model._name),
            'field_id': ctx.env['ir.model.fields']._get(Model._name, field.name).id,
            'threshold_label': 'big_table',
            'threshold_value': big_table,
            'found_value': rows,
            'references': references,
            'evidence': {'uses': len(references), 'column': field.name},
            'fix_kind': 'create_index',
            'fix_params': {'table': Model._table, 'columns': [field.name], 'index_name': index_name},
        }
