"""A5: stale planner statistics.

Requirements: FR-OPA-0011
"""
from .base import Check, register, table_models


@register
class AnalyzeCheck(Check):
    """Tables whose planner statistics are old or far behind the changes made since."""

    code = 'A5'

    def run(self, ctx):
        """Report each table of enough rows with an old analyze and changes since, or with many changes since.

        Requirements: FR-OPA-0011
        """
        statistics = ctx.probe.analyze_statistics()
        owners = table_models(ctx.env)
        findings = []
        for table, stats in sorted(statistics.items()):
            rule = self._rule_fired(ctx, stats) if stats['live_rows'] >= ctx.thresholds['min_rows'] else None
            if rule:
                findings.append(self._finding(ctx, table, stats, rule, owners.get(table)))
        return findings

    @staticmethod
    def _modified_percent(stats):
        """Rows modified since the latest analyze as a percent of the live rows."""
        return 100.0 * stats['modified_rows'] / stats['live_rows'] if stats['live_rows'] else 0.0

    def _rule_fired(self, ctx, stats):
        """The threshold label that makes the table stale ('modified_percent' or 'analyze_age'), or None."""
        if self._modified_percent(stats) > ctx.thresholds['modified_percent']:
            return 'modified_percent'
        too_old = stats['analyze_age'] is None or stats['analyze_age'] > ctx.thresholds['analyze_age']
        return 'analyze_age' if too_old and stats['modified_rows'] > 0 else None

    def _finding(self, ctx, table, stats, rule, model_name):
        """The finding of one table with its latest analyze, the rows modified since and the rule that fired."""
        modified_percent = round(self._modified_percent(stats), 1)
        days = None if stats['analyze_age'] is None else round(stats['analyze_age'], 1)
        found = modified_percent if rule == 'modified_percent' else days
        return {
            'identity_key': f'A5:{table}',
            'message': ctx.env._(
                'Table %(table)s has %(modified)d rows modified since its latest analyze (%(percent)s%%).',
                table=table, modified=stats['modified_rows'], percent=modified_percent)
            if days is not None or rule == 'modified_percent' else ctx.env._(
                'Table %(table)s was never analyzed and has %(modified)d rows modified.',
                table=table, modified=stats['modified_rows']),
            'model_id': ctx.env['ir.model']._get_id(model_name) if model_name else False,
            'table_name': table,
            'row_estimate': stats['live_rows'],
            'threshold_label': rule,
            'threshold_value': ctx.thresholds[rule],
            'found_value': found or 0.0,
            'evidence': {
                'last_analyze': stats['last_analyze'], 'modified_rows': stats['modified_rows'],
                'modified_pct': modified_percent, 'rows_estimate': stats['live_rows'],
                'days_since_analyze': days,
            },
            'fix_kind': 'analyze',
            'fix_params': {'table': table},
        }
