"""A4: tables that need vacuum.

Requirements: FR-OPA-0009, FR-OPA-0010
"""
from .base import Check, register

BLOAT_EXTENSION = 'pgstattuple'
# Share of the rows kept free of dead rows by the proposed table setting
AUTOVACUUM_SCALE_FACTOR = 0.01


@register
class VacuumCheck(Check):
    """Tables whose dead rows are many and a large share of all their rows."""

    code = 'A4'

    def run(self, ctx):
        """Report each table over both dead-row thresholds, adding the approximate dead space when readable.

        Requirements: FR-OPA-0009, FR-OPA-0010, BR-OPA-0004
        """
        statistics = ctx.probe.vacuum_statistics()
        flagged = {
            table: dead_percent for table, stats in sorted(statistics.items())
            if (dead_percent := self._dead_percent(stats)) > ctx.thresholds['dead_percent']
            and stats['dead_rows'] > ctx.thresholds['dead_rows']}
        dead_space = self._dead_space(ctx, flagged)
        return [
            self._finding(ctx, table, statistics[table], dead_percent, dead_space.get(table))
            for table, dead_percent in flagged.items()]

    @staticmethod
    def _dead_percent(stats):
        """Dead rows as a percent of live and dead rows."""
        total = stats['live_rows'] + stats['dead_rows']
        return 100.0 * stats['dead_rows'] / total if total else 0.0

    def _dead_space(self, ctx, flagged):
        """Approximate dead bytes of the flagged tables; records why the part is skipped when it cannot be had.

        Requirements: FR-OPA-0010
        """
        if not flagged:
            return {}
        if BLOAT_EXTENSION not in ctx.probe.installed_extensions():
            ctx.outcome['part_skip_reason'] = ctx.env._(
                'Exact bloat skipped: the %s extension is not installed.', BLOAT_EXTENSION)
            return {}
        measured = ctx.probe.approximate_dead_space(list(flagged))
        if measured is None:
            ctx.outcome['part_skip_reason'] = ctx.env._(
                'Exact bloat skipped: the %s extension is not readable.', BLOAT_EXTENSION)
            return {}
        return measured

    def _finding(self, ctx, table, stats, dead_percent, dead_space):
        """The finding of one table with its dead and live rows, vacuum times and a table setting as the fix."""
        dead_percent = round(dead_percent, 1)
        evidence = {
            'dead_rows': stats['dead_rows'], 'live_rows': stats['live_rows'], 'dead_pct': dead_percent,
            'last_vacuum': stats['last_vacuum'], 'last_autovacuum': stats['last_autovacuum'],
            'rows_estimate': stats['live_rows'],
        }
        if dead_space is not None:
            evidence['dead_space_bytes_approx'] = dead_space
            evidence['bloat_measure'] = 'approximate'
        return {
            'identity_key': f'A4:{table}',
            'message': ctx.env._(
                'Table %(table)s has %(dead)d dead rows, %(percent)s%% of its rows.',
                table=table, dead=stats['dead_rows'], percent=dead_percent),
            'table_name': table,
            'row_estimate': stats['live_rows'],
            'threshold_label': 'dead_percent',
            'threshold_value': ctx.thresholds['dead_percent'],
            'found_value': dead_percent,
            'evidence': evidence,
            'fix_kind': 'autovacuum',
            'fix_params': {
                'table': table,
                'settings': {'autovacuum_vacuum_scale_factor': AUTOVACUUM_SCALE_FACTOR},
            },
        }
