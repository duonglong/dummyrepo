"""B6: models with many tracked fields and many updates.

Requirements: FR-OPA-0021
"""
from .base import Check, register

DISCUSS_MODULE = 'mail'


@register
class TrackingCheck(Check):
    """Busy models that compare and log the value of many fields on every save."""

    code = 'B6'

    def needs(self, ctx):
        """Skip without the Discuss app: no field is tracked then.

        Requirements: FR-OPA-0021, FR-OPA-0003
        """
        # module state is read for the audit, whoever started it
        installed = ctx.env['ir.module.module'].sudo().search_count(
            [('name', '=', DISCUSS_MODULE), ('state', '=', 'installed')])
        return None if installed else ctx.env._('Discuss app not installed')

    def tracked_fields(self, env):
        """Names of the tracked fields per model name."""
        return {
            model_name: sorted(
                name for name, field in env[model_name]._fields.items() if getattr(field, 'tracking', None))
            for model_name in env.registry
        }

    def run(self, ctx):
        """Report each model with more tracked fields than allowed and more updates than allowed.

        Requirements: FR-OPA-0021
        """
        updates_by_table = ctx.probe.table_updates()
        rows_by_table = ctx.probe.estimated_rows()
        max_fields = ctx.thresholds['tracked_fields']
        max_updates = ctx.thresholds['updates']
        findings = []
        for model_name, tracked in self.tracked_fields(ctx.env).items():
            table = ctx.env[model_name]._table
            updates = updates_by_table.get(table, 0)
            if len(tracked) > max_fields and updates > max_updates:
                findings.append({
                    'identity_key': f'B6:{model_name}',
                    'message': ctx.env._(
                        '%(model)s has %(count)d tracked fields and %(updates)d updates since the statistics reset.',
                        model=model_name, count=len(tracked), updates=updates),
                    'model_id': ctx.env['ir.model']._get_id(model_name),
                    'table_name': table,
                    'row_estimate': rows_by_table.get(table),
                    'found_value': len(tracked),
                    'threshold_label': 'tracked_fields',
                    'threshold_value': max_fields,
                    'evidence': {
                        'tracked_fields': tracked, 'updates': updates, 'updates_threshold': max_updates,
                    },
                })
        return findings
