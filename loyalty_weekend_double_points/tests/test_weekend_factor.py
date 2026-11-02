from datetime import datetime

from odoo.tests import tagged

from .common import WeekendPointsCommon


@tagged('post_install', '-at_install')
class TestWeekendFactor(WeekendPointsCommon):
    """Unit tests of the shared doubling rule."""

    def _program(self, weekend=True, program_type='loyalty'):
        return self.env['loyalty.program'].create({
            'name': 'P', 'program_type': program_type, 'weekend_double_points': weekend})

    def test_factor_by_day_in_company_timezone(self):
        """Requirements: BR-DLP-001, BR-DLP-002, FR-DLP-004."""
        company = self.env.company
        company.tz = 'Europe/Brussels'
        program = self._program()
        cases = [
            (datetime(2026, 11, 4, 12), 1),    # Wednesday
            (datetime(2026, 11, 6, 22, 30), 1),  # Friday 23:30 local
            (datetime(2026, 11, 6, 23, 30), 2),  # Saturday 00:30 local
            (datetime(2026, 11, 8, 22, 30), 2),  # Sunday 23:30 local
            (datetime(2026, 11, 8, 23, 0), 1),   # Monday 00:00 local
        ]
        for date, expected in cases:
            self.assertEqual(program._get_weekend_factor(date, company), expected, date)

    def test_factor_is_one_when_option_off(self):
        """Requirements: FR-DLP-004."""
        program = self._program(weekend=False)
        self.assertEqual(program._get_weekend_factor(datetime(2026, 11, 7, 12), self.env.company), 1)

    def test_option_reset_for_other_types(self):
        """Requirements: BR-DLP-003."""
        program = self._program()
        program.program_type = 'gift_card'
        self.assertFalse(program.weekend_double_points)
        self.assertEqual(program._get_weekend_factor(datetime(2026, 11, 7, 12), self.env.company), 1)
