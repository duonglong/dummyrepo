from odoo.tests import TransactionCase, tagged
from odoo.tools import SQL

from ..services.patterns import pattern_of
from ..services.profiler import _pattern_sql


@tagged('post_install', '-at_install')
class TestPatterns(TransactionCase):
    """Format pattern of a text value."""

    def test_pattern_of_examples(self):
        """Requirements: BR-DPA-0003 (letters A, digits 9, others kept, ends trimmed)."""
        self.assertEqual(pattern_of('BE0477.472.701'), 'AA9999.999.999')
        self.assertEqual(pattern_of('555-0100'), '999-9999')
        self.assertEqual(pattern_of('  ab 12 \n'), 'AA 99')

    def test_sql_pattern_matches_reference(self):
        """Requirements: BR-DPA-0003 (the SQL expression gives the same pattern as pattern_of)."""
        for text in ('BE0477.472.701', ' 555-0100 ', 'Éric 7', '+32 470'):
            sql = _pattern_sql(SQL("%s", text))
            self.env.cr.execute(SQL("SELECT %s", sql))
            self.assertEqual(self.env.cr.fetchone()[0], pattern_of(text))
