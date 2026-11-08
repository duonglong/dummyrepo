from unittest.mock import patch

from .acceptance_base import AuditAcceptanceCase

MB = 1024 * 1024


def _index(name, columns, **kw):
    values = {
        'name': name, 'table': 'x_line', 'columns': columns, 'unique': False, 'primary': False,
        'constraint': False, 'predicate': '', 'method': 'btree', 'size': 5 * MB, 'idx_scan': 10,
    }
    values.update(kw)
    return values


class TestA3Own(AuditAcceptanceCase):
    """Requirements: FR-OPA-0007, FR-OPA-0008 (T-005)."""

    def _run_a3(self, indexes, kind=None):
        probe = type(self.env['perf.audit.probe'])
        with patch.object(probe, 'index_definitions', return_value=indexes):
            return self.findings(self.start_run(), 'A3', kind=kind)

    def test_unused_index_of_five_megabytes(self):
        """FR-OPA-0007 / SC-OPA-0007-1: a non-unique 5 MB index with no scan is named with its size."""
        found = self._run_a3([_index('x_idx', ['x_date'], idx_scan=0)])
        self.assertEqual(found.mapped('index_name'), ['x_idx'])
        self.assertEqual(found.evidence['size_mb'], 5)
        self.assertEqual(found.fix_params, {'table': 'x_line', 'index_name': 'x_idx'})

    def test_indexes_that_guard_data_or_are_small_or_used(self):
        """FR-OPA-0007 / SC-OPA-0007-2: unique, primary, constraint, small and scanned indexes are not reported."""
        found = self._run_a3([
            _index('uq', ['a'], idx_scan=0, unique=True),
            _index('pk', ['id'], idx_scan=0, primary=True, unique=True),
            _index('cn', ['b'], idx_scan=0, constraint=True),
            _index('small', ['c'], idx_scan=0, size=MB - 1),
            _index('used', ['d'], idx_scan=1),
        ])
        self.assertFalse(found)

    def test_duplicate_leading_columns_name_both_indexes(self):
        """FR-OPA-0008 / SC-OPA-0008-1: (partner) is covered by (partner, date)."""
        found = self._run_a3([_index('short', ['p']), _index('long', ['p', 'd'])], kind='duplicate')
        self.assertEqual(found.mapped('index_name'), ['short'])
        self.assertEqual(found.references['indexes'], ['short', 'long'])
        self.assertEqual(found.fix_kind, 'drop_index')

    def test_other_order_or_condition_is_not_duplicate(self):
        """FR-OPA-0008 / SC-OPA-0008-2: a different column order, condition or method is no duplicate."""
        found = self._run_a3([
            _index('a', ['p']), _index('b', ['d', 'p']),
            _index('c', ['q']), _index('d', ['q', 'e'], predicate='(e > 0)'),
            _index('e', ['r']), _index('f', ['r', 'g'], method='hash'),
        ], kind='duplicate')
        self.assertFalse(found)

    def test_unique_index_is_never_the_redundant_one(self):
        """FR-OPA-0008: a unique index covered by a longer one is kept; the non-unique twin is dropped."""
        found = self._run_a3([
            _index('uq', ['p'], unique=True), _index('long', ['p', 'd']),
            _index('twin_a', ['t'], unique=True), _index('twin_b', ['t']),
        ], kind='duplicate')
        self.assertEqual(found.mapped('index_name'), ['twin_b'])

    def test_identical_non_unique_indexes_report_the_later_name(self):
        """FR-OPA-0008: of two identical indexes only the later name is proposed for removal."""
        found = self._run_a3([_index('a_idx', ['p']), _index('b_idx', ['p'])], kind='duplicate')
        self.assertEqual(found.mapped('index_name'), ['b_idx'])
