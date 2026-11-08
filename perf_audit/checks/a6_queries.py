"""A6: most expensive queries mapped to models.

Requirements: FR-OPA-0012, FR-OPA-0003
"""
import re

from ..models.perf_audit_finding import QUERY_TEXT_LIMIT
from .base import Check, register, table_models

STATEMENTS_EXTENSION = 'pg_stat_statements'
IDENTIFIER = re.compile(r'\w+')


@register
class ExpensiveQueriesCheck(Check):
    """The queries with the highest total execution time, with the models their tables belong to."""

    code = 'A6'

    def needs(self, ctx):
        """Return why the check cannot run: the query statistics extension is missing or unreadable.

        Requirements: FR-OPA-0003, FR-OPA-0012
        """
        if STATEMENTS_EXTENSION not in ctx.probe.installed_extensions():
            return ctx.env._('The %s extension is not installed.', STATEMENTS_EXTENSION)
        if not ctx.probe.statements_readable():
            return ctx.env._('The %s extension is not readable.', STATEMENTS_EXTENSION)
        return None

    def run(self, ctx):
        """Report the top queries of the database by total execution time, most expensive first.

        Requirements: FR-OPA-0012, NFR-OPA-0005
        """
        top = int(ctx.thresholds['top'])
        owners = table_models(ctx.env)
        statements = ctx.probe.top_statements(top)
        return [
            self._finding(ctx, rank, statement, self._tables_of(statement['query'], owners), owners)
            for rank, statement in enumerate(statements, start=1)]

    @staticmethod
    def _tables_of(query, owners):
        """Tables of the audited models that the query names, in order of first mention."""
        return list(dict.fromkeys(word for word in IDENTIFIER.findall(query) if word in owners))

    def _finding(self, ctx, rank, statement, tables, owners):
        """The finding of one query: its calls and times, the tables it names and their models."""
        models = [owners[table] for table in tables]
        return {
            'identity_key': f"A6:{statement['query_id']}",
            'message': ctx.env._(
                'Query %(rank)s by total time: %(total).1f ms over %(calls)s calls (mean %(mean).3f ms).',
                rank=rank, total=statement['total_time'], calls=statement['calls'],
                mean=statement['mean_time']),
            'model_id': ctx.env['ir.model']._get_id(models[0]) if models else False,
            'table_name': tables[0] if tables else False,
            'query_id': statement['query_id'],
            'query_text': statement['query'],
            'threshold_label': 'top',
            'threshold_value': ctx.thresholds['top'],
            'found_value': rank,
            'evidence': {
                'rank': rank, 'query': statement['query'][:QUERY_TEXT_LIMIT], 'calls': statement['calls'],
                'total_time_ms': statement['total_time'], 'mean_time_ms': statement['mean_time'],
                'tables': tables, 'models': models,
            },
            'references': {'models': models},
            'code_suggestion': ctx.env._(
                'Look in checks A1, A2 and B3 for the filter columns of %(models)s that this query reads.',
                models=', '.join(models) or ', '.join(tables) or statement['query_id']),
        }
