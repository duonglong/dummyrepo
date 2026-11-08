"""B2: stored computed fields whose dependencies fan out through a many2one.

Requirements: FR-OPA-0017
"""
from .base import Check, register


@register
class FanOutCheck(Check):
    """Stored computes recomputed in bulk when one target record of a many2one changes."""

    code = 'B2'

    def run(self, ctx):
        """Report each stored compute whose many2one dependency has a fan-out above the threshold.

        Requirements: FR-OPA-0017, BR-OPA-0001
        """
        rows_by_table = ctx.probe.estimated_rows()
        findings = []
        for model_name in ctx.env.registry:
            model = ctx.env[model_name]
            if model._abstract or not model._auto:
                continue
            rows = rows_by_table.get(model._table, 0)
            if rows <= ctx.thresholds['big_table']:
                continue
            for field in model._fields.values():
                if not (field.store and field.compute) or field.inherited:
                    continue
                if finding := self._finding(ctx, model, field, rows):
                    findings.append(finding)
        return findings

    def _finding(self, ctx, model, field, rows):
        """The finding of one stored compute, built from its widest many2one dependency, or None."""
        depends = sorted(field.get_depends(model)[0])
        fan_outs = self._fan_outs(ctx, model, depends, rows)
        if not fan_outs:
            return None
        link, fan_out = max(fan_outs.items(), key=lambda item: item[1])
        threshold = ctx.thresholds['fan_out']
        if fan_out <= threshold:
            return None
        return {
            'identity_key': f'B2:{model._name}.{field.name}',
            'message': ctx.env._(
                'Stored compute %(field)s of %(model)s is recomputed for about %(fan_out)d records '
                'whenever one record of %(link)s changes (estimated rows per distinct value).',
                field=field.name, model=model._name, fan_out=fan_out, link=link),
            'model_id': ctx.env['ir.model']._get_id(model._name),
            'field_id': ctx.env['ir.model.fields']._get(model._name, field.name).id,
            'table_name': model._table,
            'row_estimate': rows,
            'found_value': fan_out,
            'threshold_label': 'fan_out',
            'threshold_value': threshold,
            'evidence': {
                'many2one': link, 'rows_estimate': rows, 'fan_out': fan_out,
                'depends': depends,
            },
            'code_suggestion': ctx.env._(
                'Narrow the dependency of %(model)s.%(field)s on %(link)s, make it non-stored '
                'or refresh it by a scheduled action.',
                model=model._name, field=field.name, link=link),
        }

    def _fan_outs(self, ctx, model, depends, rows):
        """Estimated rows per distinct value, for each stored many2one the compute depends on through a path."""
        result = {}
        for path in depends:
            link, _dot, rest = path.partition('.')
            target = model._fields.get(link)
            if not rest or target is None or target.type != 'many2one' or not target.store:
                continue
            distinct = ctx.probe.distinct_values(model._table, link)
            if distinct:
                result[link] = rows / distinct
        return result
