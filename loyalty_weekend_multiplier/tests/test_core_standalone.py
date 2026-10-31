from datetime import datetime

from odoo.exceptions import ValidationError
from odoo.modules.module import get_manifest

from .common import WeekendMultiplierCase


class TestCoreStandalone(WeekendMultiplierCase):
    """The core module works on Loyalty alone, with base groups only."""

    def _promotion(self):
        return self.env['loyalty.program'].create({
            'name': 'Promo', 'program_type': 'promotion'})

    def test_store_2_50(self):
        """Requirements: FR-LAP-013 - SC-LAP-013-2: 2.50 is saved and read back."""
        self.program.weekend_multiplier = 2.5
        self.env.flush_all()
        self.program.invalidate_recordset()
        self.assertEqual(self.program.weekend_multiplier, 2.5)

    def test_refuse_zero_and_negative(self):
        """Requirements: FR-LAP-013 - SC-LAP-013-3: 0 and -1 are refused naming the field."""
        for value in (0, -1):
            with self.assertRaisesRegex(ValidationError, 'Weekend Multiplier'):
                self.program.weekend_multiplier = value
                self.env.flush_all()
            self.program.invalidate_recordset()

    def test_weekend_points_by_user_timezone(self):
        """Requirements: FR-LAP-013 - the day is read in the timezone of the user."""
        self.program.weekend_multiplier = 2.0
        points = self.program._lwm_weekend_points
        self.assertEqual(points(100.0, datetime(2026, 10, 23, 23, 30), self.user_brussels), 200.0)
        self.assertEqual(points(100.0, datetime(2026, 10, 24, 2, 0), self.user_new_york), 100.0)
        self.assertEqual(points(100.0, datetime(2026, 10, 25, 23, 59), self.user_no_tz), 200.0)
        self.assertEqual(points(100.0, datetime(2026, 10, 21, 10, 0), self.user_no_tz), 100.0)

    def test_weekend_points_rounding(self):
        """Requirements: FR-LAP-013 - 10.99 with 1.15 on a Saturday gives 12.63."""
        self.program.weekend_multiplier = 1.15
        result = self.program._lwm_weekend_points(
            10.99, datetime(2026, 10, 24, 10, 0), self.user_no_tz)
        self.assertEqual(result, 12.63)

    def test_promotion_unchanged(self):
        """Requirements: FR-LAP-010 - a promotion program keeps its points on a Sunday."""
        promo = self._promotion()
        promo.weekend_multiplier = 3.0
        result = promo._lwm_weekend_points(
            100.0, datetime(2026, 10, 25, 10, 0), self.user_no_tz)
        self.assertEqual(result, 100.0)

    def test_manifest_depends_only_on_loyalty(self):
        """Requirements: FR-LAP-013, NFR-COMP-001 - depends is exactly loyalty."""
        self.assertEqual(get_manifest('loyalty_weekend_multiplier')['depends'], ['loyalty'])
