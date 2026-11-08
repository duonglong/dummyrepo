"""B5: costly default sort order of a big table.

Requirements: FR-OPA-0020
"""
from .b3_domain_index import order_field_names
from .base import Check, register


@register
class OrderCheck(Check):
    """Report models of big tables whose default order joins, or sorts on a column with no leading index.

    Requirements: FR-OPA-0020
    """

    code = 'B5'

    def run(self, ctx):
        """Return one finding per model whose `_order` has a costly part.

        Requirements: FR-OPA-0020
        """
        env = ctx.env
        rows = ctx.probe.estimated_rows()
        indexed = ctx.probe.indexed_leading_columns()
        big_table = ctx.thresholds['big_table']
        findings = []
        for name, Model in sorted(env.registry.items()):
            if not Model._auto or Model._abstract or rows.get(Model._table, 0) <= big_table:
                continue
            costly = self._costly_parts(Model, indexed)
            if costly:
                findings.append(self._finding(ctx, Model, costly, rows[Model._table], big_table))
        return findings

    def _costly_parts(self, Model, indexed):
        """(field name, reason) of every part of the order that is costly."""
        costly = []
        for name in order_field_names(Model._order):
            field = Model._fields.get(name)
            if field is None:
                continue
            if field.type == 'many2one':
                costly.append((name, 'many2one'))
            elif not field.store:
                costly.append((name, 'not stored'))
            elif (Model._table, name) not in indexed:
                costly.append((name, 'no index'))
        return costly

    def _finding(self, ctx, Model, costly, rows, big_table):
        """The finding of one model."""
        names = ', '.join(name for name, _reason in costly)
        return {
            'identity_key': 'B5:%s' % Model._name,
            'message': 'Default order of %s is costly on %s' % (Model._name, names),
            'table_name': Model._table,
            'row_estimate': rows,
            'model_id': ctx.env['ir.model']._get_id(Model._name),
            'threshold_label': 'big_table',
            'threshold_value': big_table,
            'found_value': rows,
            'evidence': {'order': Model._order, 'costly_fields': [
                {'field': name, 'reason': reason} for name, reason in costly]},
            'code_suggestion': (
                "Change _order of %s (now '%s') to use indexed columns of its own table instead of %s."
                % (Model._name, Model._order, names)),
        }
