"""A3: unused and duplicate indexes.

Requirements: FR-OPA-0007, FR-OPA-0008
"""
from .base import Check, register

BYTES_PER_MEGABYTE = 1024 * 1024


def _guards_data(index):
    """True for an index that enforces uniqueness or a constraint, which is never proposed for removal."""
    return index['unique'] or index['primary'] or index['constraint']


@register
class IndexesCheck(Check):
    """Indexes that cost writes and serve no read: never scanned, or covered by another index."""

    code = 'A3'

    def run(self, ctx):
        """Report the unused indexes, then the duplicate ones.

        Requirements: FR-OPA-0007, FR-OPA-0008
        """
        indexes = sorted(ctx.probe.index_definitions(), key=lambda index: (index['table'], index['name']))
        minimum_bytes = ctx.thresholds['min_index_size'] * BYTES_PER_MEGABYTE
        findings = [
            self._unused_finding(ctx, index) for index in indexes
            if index['idx_scan'] == 0 and not _guards_data(index) and index['size'] >= minimum_bytes]
        by_table = {}
        for index in indexes:
            by_table.setdefault(index['table'], []).append(index)
        for index in indexes:
            if (cover := self._covering_index(index, by_table[index['table']])) is not None:
                findings.append(self._duplicate_finding(ctx, index, cover))
        return findings

    @staticmethod
    def _covers(other, index):
        """True when `other` makes `index` redundant: same table, kind and condition, leading columns.

        Requirements: FR-OPA-0008
        """
        columns, other_columns = index['columns'], other['columns']
        if other is index or other['table'] != index['table']:
            return False
        if (other['method'], other['predicate']) != (index['method'], index['predicate']):
            return False
        if other_columns[:len(columns)] != columns:
            return False
        if len(other_columns) > len(columns):
            return True
        # identical columns: keep the one that guards data, else the first by name
        return _guards_data(other) or other['name'] < index['name']

    def _covering_index(self, index, indexes):
        """The index that makes `index` redundant (the widest, then the first by name), or None."""
        if _guards_data(index):
            return None
        covers = [other for other in indexes if self._covers(other, index)]
        return min(covers, key=lambda other: (-len(other['columns']), other['name']), default=None)

    def _unused_finding(self, ctx, index):
        """The finding of one index with no scan since the statistics reset."""
        size_mb = index['size'] / BYTES_PER_MEGABYTE
        return {
            'identity_key': f"A3:unused:{index['name']}",
            'kind': 'unused',
            'message': ctx.env._(
                'Index %(index)s on %(table)s (%(size).1f MB) was not scanned since the statistics reset.',
                index=index['name'], table=index['table'], size=size_mb),
            'table_name': index['table'],
            'index_name': index['name'],
            'found_value': size_mb,
            'threshold_label': 'min_index_size',
            'threshold_value': ctx.thresholds['min_index_size'],
            'stats_reset_date': ctx.run.stats_reset_date,
            'evidence': {
                'size_bytes': index['size'], 'size_mb': size_mb, 'idx_scan': index['idx_scan'],
                'columns': index['columns'],
            },
            'fix_kind': 'drop_index',
            'fix_params': {'table': index['table'], 'index_name': index['name']},
        }

    def _duplicate_finding(self, ctx, index, cover):
        """The finding of one redundant index, naming the index that covers it."""
        return {
            'identity_key': f"A3:duplicate:{index['name']}",
            'kind': 'duplicate',
            'message': ctx.env._(
                'Index %(index)s on %(table)s (%(columns)s) is covered by index %(cover)s (%(cover_columns)s).',
                index=index['name'], table=index['table'], columns=', '.join(index['columns']),
                cover=cover['name'], cover_columns=', '.join(cover['columns'])),
            'table_name': index['table'],
            'index_name': index['name'],
            'stats_reset_date': ctx.run.stats_reset_date,
            'evidence': {
                'index': index['name'], 'columns': index['columns'], 'size_bytes': index['size'],
                'covered_by': cover['name'], 'covered_by_columns': cover['columns'],
            },
            'references': {'indexes': [index['name'], cover['name']]},
            'fix_kind': 'drop_index',
            'fix_params': {'table': index['table'], 'index_name': index['name']},
        }
