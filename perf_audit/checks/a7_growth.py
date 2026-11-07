"""A7: tables that only grow.

Requirements: FR-OPA-0013
"""
from .base import BYTES_PER_MEGABYTE, Check, register

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
        in_database = ATTACHMENT_TABLE in sizes and ctx.probe.attachments_in_database()
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
            findings.append(self._finding(
                ctx, table, label, found, sizes[table], previous.get(table), growth,
                in_database and table == ATTACHMENT_TABLE))
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

    def _finding(self, ctx, table, label, found, size, previous_size, growth, files_in_database):
        """The finding of one table: its size now and before, the growth and a purge or archive suggestion."""
        growth_percent = None if growth is None else round(growth, 1)
        suggestion = ctx.env._('Purge or archive old rows of %s.', table)
        evidence = {
            'size_bytes': size, 'previous_size_bytes': previous_size, 'growth_pct': growth_percent,
            'size_mb': round(size / BYTES_PER_MEGABYTE, 1),
        }
        if files_in_database:
            evidence['files_stored_in_database'] = True
        return {
            'identity_key': f'A7:{table}',
            'message': ctx.env._(
                'Table %(table)s is %(size)s MB, growth since the previous run: %(growth)s.',
                table=table, size=evidence['size_mb'],
                growth='%s%%' % growth_percent if growth is not None else ctx.env._('unknown')),
            'table_name': table,
            'threshold_label': label,
            'threshold_value': ctx.thresholds[label],
            'found_value': found,
            'evidence': evidence,
            'code_suggestion': suggestion,
        }
