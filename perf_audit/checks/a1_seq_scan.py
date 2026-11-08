"""A1: big tables read by sequential scans.

Requirements: FR-OPA-0005
"""
from .base import Check, register, table_models


@register
class SeqScanCheck(Check):
    """Big tables whose sequential scans outnumber their index scans."""

    code = 'A1'

    def run(self, ctx):
        """Report each big table with more sequential than index scans, most rows read first.

        Requirements: FR-OPA-0005, BR-OPA-0001
        """
        big_table = ctx.thresholds['big_table']
        owners = table_models(ctx.env)
        statistics = ctx.probe.scan_statistics()
        scanned = sorted(
            (table for table, stats in statistics.items()
             if stats['live_rows'] > big_table and stats['seq_scan'] > stats['idx_scan']),
            key=lambda table: (-statistics[table]['seq_tup_read'], table))
        return [self._finding(ctx, table, statistics[table], owners.get(table)) for table in scanned]

    def _finding(self, ctx, table, stats, model_name):
        """The finding of one table, naming its model when it has one."""
        average_rows = stats['seq_tup_read'] // stats['seq_scan']
        return {
            'identity_key': f'A1:{table}',
            'message': ctx.env._(
                'Table %(table)s (about %(rows)d rows) was read by %(seq)d sequential scans against '
                '%(idx)d index scans.',
                table=table, rows=stats['live_rows'], seq=stats['seq_scan'], idx=stats['idx_scan']),
            'model_id': ctx.env['ir.model']._get_id(model_name) if model_name else False,
            'table_name': table,
            'row_estimate': stats['live_rows'],
            'found_value': stats['live_rows'],
            'threshold_label': 'big_table',
            'threshold_value': ctx.thresholds['big_table'],
            'stats_reset_date': ctx.run.stats_reset_date,
            'evidence': {
                'seq_scan': stats['seq_scan'], 'idx_scan': stats['idx_scan'],
                'seq_tup_read': stats['seq_tup_read'], 'avg_rows_per_seq_scan': average_rows,
                'rows_estimate': stats['live_rows'], 'total_size_bytes': stats['total_size'],
            },
            'code_suggestion': ctx.env._(
                'Find the column that %(table)s is filtered on with checks A2, B3 or A6, then create an index on it.',
                table=table),
        }
