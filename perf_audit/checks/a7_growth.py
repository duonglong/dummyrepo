"""A7: tables that only grow.

Requirements: FR-OPA-0013
"""
from .base import BYTES_PER_MEGABYTE, Check, register, table_models

# Tables that grow with use and are rarely cleaned: messages, tracking values, bus
# notifications, server logs, outgoing mails and attachments
GROWING_TABLES = (
    'mail_message', 'mail_tracking_value', 'bus_bus', 'ir_logging', 'mail_mail', 'ir_attachment')
ATTACHMENT_TABLE = 'ir_attachment'


@register
class GrowthCheck(Check):
    """Tables of the growing kind that are big, or grew fast since the previous completed run."""

    code = 'A7'

    def run(self, ctx):
        """Report each listed table above the size threshold or the growth threshold, and save every size.

        Requirements: FR-OPA-0013
        """
        sizes = ctx.probe.table_sizes(GROWING_TABLES)
        ctx.outcome['measures'] = sizes
        previous = self._previous_sizes(ctx)
        owners = table_models(ctx.env)
        findings = []
        for table in GROWING_TABLES:
            if table not in sizes:
                continue
            growth = self._growth_percent(sizes[table], previous.get(table))
            if sizes[table] / BYTES_PER_MEGABYTE > ctx.thresholds['size']:
                label, found = 'size', sizes[table] / BYTES_PER_MEGABYTE
            elif growth is not None and growth > ctx.thresholds['growth']:
                label, found = 'growth', growth
            else:
                continue
            evidence = {
                'size_bytes': sizes[table], 'previous_size_bytes': previous.get(table),
                'growth_pct': None if growth is None else round(growth, 1),
                'size_mb': round(sizes[table] / BYTES_PER_MEGABYTE, 1),
            }
            if table == ATTACHMENT_TABLE and ctx.probe.attachments_in_database():
                evidence['files_stored_in_database'] = True
            findings.append(self._finding(ctx, table, (label, found), evidence, owners.get(table)))
        return findings

    @staticmethod
    def _growth_percent(size, previous_size):
        """Growth of a table in percent of its previous size, or None without a usable previous size."""
        if not previous_size:
            return None
        return 100.0 * (size - previous_size) / previous_size

    def _previous_sizes(self, ctx):
        """Table sizes measured by the previous completed run's A7, or an empty dict."""
        # the result lines belong to the audit, whoever runs it
        previous = ctx.env['perf.audit.check.result'].sudo().search([
            ('code', '=', self.code), ('state', '=', 'done'),
            ('run_id.state', '=', 'done'), ('run_id', '<', ctx.run.id),
        ], order='run_id desc', limit=1)
        return previous.measures or {}

    def _finding(self, ctx, table, crossed, evidence, model_name):
        """The finding of one table: its sizes, the threshold crossed as (label, value found) and a purge suggestion."""
        label, found = crossed
        growth = evidence['growth_pct']
        return {
            'identity_key': f'A7:{table}',
            'message': ctx.env._(
                'Table %(table)s is %(size)s MB, growth since the previous run: %(growth)s.',
                table=table, size=evidence['size_mb'],
                growth='%s%%' % growth if growth is not None else ctx.env._('unknown')),
            'model_id': ctx.env['ir.model']._get_id(model_name) if model_name else False,
            'table_name': table,
            'threshold_label': label,
            'threshold_value': ctx.thresholds[label],
            'found_value': found,
            'evidence': evidence,
            'code_suggestion': ctx.env._('Purge or archive old rows of %s.', table),
        }
