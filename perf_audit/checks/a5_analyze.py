"""A5: stale planner statistics.

Requirements: FR-OPA-0011
"""
from .base import Check, register


@register
class AnalyzeCheck(Check):
    """Tables whose planner statistics are old or far behind the changes made since."""

    code = 'A5'

    def run(self, ctx):
        """Report each table of enough rows with an old analyze and changes since, or with many changes since.

        Requirements: FR-OPA-0011
        """
        statistics = ctx.probe.analyze_statistics()
        return [
            self._finding(ctx, table, stats) for table, stats in sorted(statistics.items())
            if stats['live_rows'] >= ctx.thresholds['min_rows'] and self._is_stale(ctx, stats)]

    @staticmethod
    def _modified_percent(stats):
        """Rows modified since the latest analyze as a percent of the live rows."""
        return 100.0 * stats['modified_rows'] / stats['live_rows'] if stats['live_rows'] else 0.0

    def _is_stale(self, ctx, stats):
        """True when the latest analyze is too old with changes since, or the changes are too many."""
        too_old = stats['analyze_age'] is None or stats['analyze_age'] > ctx.thresholds['analyze_age']
        return (too_old and stats['modified_rows'] > 0
                or self._modified_percent(stats) > ctx.thresholds['modified_percent'])

    def _finding(self, ctx, table, stats):
        """The finding of one table with its latest analyze and the rows modified since."""
        modified_percent = round(self._modified_percent(stats), 1)
        return {
            'identity_key': f'A5:{table}',
            'message': ctx.env._(
                'Table %(table)s has %(modified)d rows modified since its latest analyze (%(percent)s%%).',
                table=table, modified=stats['modified_rows'], percent=modified_percent),
            'table_name': table,
            'row_estimate': stats['live_rows'],
            'threshold_label': 'modified_percent',
            'threshold_value': ctx.thresholds['modified_percent'],
            'found_value': modified_percent,
            'evidence': {
                'last_analyze': stats['last_analyze'], 'modified_rows': stats['modified_rows'],
                'modified_pct': modified_percent, 'rows_estimate': stats['live_rows'],
                'days_since_analyze': (
                    None if stats['analyze_age'] is None else round(stats['analyze_age'], 1)),
            },
            'fix_kind': 'analyze',
            'fix_params': {'table': table},
        }
