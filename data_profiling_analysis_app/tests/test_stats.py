from datetime import datetime

from odoo.tests import TransactionCase, tagged

from ..services import profiler


@tagged('post_install', '-at_install')
class TestStats(TransactionCase):
    """Statistics, outdated records and the empty model through the engine."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.cr.execute("UPDATE res_partner SET active = false")
        cls.env.invalidate_all()

    def _age(self, partner, when):
        self.env.flush_all()  # a pending recomputation would write the record again, after the aging
        self.env.cr.execute("UPDATE res_partner SET write_date = %s WHERE id = %s", (when, partner.id))
        self.env.invalidate_all()

    def test_outdated_period_boundary(self):
        """Requirements: BR-DPA-0005, FR-DPA-0011 (exactly the period ago is not outdated, a second more is)."""
        on_edge, past = self.env['res.partner'].create([{'name': 'Edge'}, {'name': 'Past'}])
        self._age(on_edge, '2026-08-27 12:00:00')
        self._age(past, '2026-08-27 11:59:59')
        result = profiler.profile(self.env, 'res.partner', ['name'], 90, datetime(2026, 11, 25, 12, 0, 0))
        self.assertEqual([f['record_name'] for f in result['outdated']], ['Past'])

    def test_no_outdated_without_period(self):
        """Requirements: FR-DPA-0011 (no period given, no outdated record is looked for)."""
        self.env['res.partner'].create({'name': 'Any'})
        self.assertEqual(profiler.profile(self.env, 'res.partner', ['name'])['outdated'], [])

    def test_statistics_absent_without_filled_values(self):
        """Requirements: FR-DPA-0016, FR-DPA-0028 (an unfilled number field has no statistics, an empty model none)."""
        result = profiler.profile(self.env, 'res.partner', ['color'])
        self.assertEqual(result['record_count'], 0)
        self.assertNotIn('min_value', result['fields'][0])
        self.env['res.partner'].create({'name': 'Zero'})
        self.env['res.partner'].create({'name': 'Date'})
        line = profiler.profile(self.env, 'res.partner', ['color'])['fields'][0]
        self.assertEqual((line['min_value'], line['max_value']), (0.0, 0.0))
