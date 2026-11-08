"""A2: stored many2one fields without an index on a big table.

Requirements: FR-OPA-0006
"""
from odoo.tools.sql import make_index_name

from .base import Check, register


@register
class Many2oneIndexCheck(Check):
    """Many2one columns of big tables that no index can serve."""

    code = 'A2'

    def run(self, ctx):
        """Report each stored many2one of a big table without index flag and not leading any index.

        Requirements: FR-OPA-0006, BR-OPA-0001
        """
        big_table = ctx.thresholds['big_table']
        rows_by_table = ctx.probe.estimated_rows()
        leading_columns = {}
        for index in ctx.probe.index_definitions():
            if index['columns']:
                leading_columns.setdefault(index['table'], set()).add(index['columns'][0])
        findings = []
        for model_name in sorted(ctx.env.registry):
            model = ctx.env[model_name]
            rows = rows_by_table.get(model._table, 0)
            if model._abstract or not model._auto or rows <= big_table:
                continue
            for field in model._fields.values():
                if self._is_unindexed(field, leading_columns.get(model._table, ())):
                    findings.append(self._finding(ctx, model, field, rows))
        return findings

    @staticmethod
    def _is_unindexed(field, leading_columns):
        """True for an own stored many2one with no index flag whose column leads no index."""
        return (
            field.type == 'many2one' and field.store and not field.inherited
            and not field.index and field.name not in leading_columns)

    def _finding(self, ctx, model, field, rows):
        """The finding of one many2one column, with its create-index fix."""
        return {
            'identity_key': f'A2:{model._name}.{field.name}',
            'message': ctx.env._(
                'Many2one %(field)s of %(model)s has no index on a table of about %(rows)d rows.',
                field=field.name, model=model._name, rows=rows),
            'model_id': ctx.env['ir.model']._get_id(model._name),
            'field_id': ctx.env['ir.model.fields']._get(model._name, field.name).id,
            'table_name': model._table,
            'row_estimate': rows,
            'found_value': rows,
            'threshold_label': 'big_table',
            'threshold_value': ctx.thresholds['big_table'],
            'evidence': {'rows_estimate': rows, 'column': field.name, 'comodel': field.comodel_name},
            'fix_kind': 'create_index',
            'fix_params': {
                'table': model._table, 'columns': [field.name],
                'index_name': make_index_name(model._table, field.name),
            },
            'code_suggestion': f'index=True on {model._name}.{field.name}',
        }
