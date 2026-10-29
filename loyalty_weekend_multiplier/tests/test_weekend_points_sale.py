from odoo import Command

from .common import WeekendMultiplierCase


class TestWeekendPointsSale(WeekendMultiplierCase):
    """Unit tests of the weekend helpers on sale.order and loyalty.program.

    Requirements: BR-LAP-001, BR-LAP-004, FR-LAP-005, FR-LAP-006
    """

    def _order(self, date_order, user):
        return self.env['sale.order'].with_user(user).create({
            'partner_id': self.partner.id,
            'date_order': date_order,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'price_unit': 100.0,
            })],
        })

    def test_brussels_friday_utc_is_saturday(self):
        """Requirements: FR-LAP-005 - Friday 23:30 UTC is Saturday in Brussels."""
        order = self._order('2026-10-23 23:30:00', self.user_brussels)
        self.assertTrue(order._is_weekend_order())

    def test_new_york_saturday_utc_is_friday(self):
        """Requirements: FR-LAP-005 - Saturday 02:00 UTC is Friday in New York."""
        order = self._order('2026-10-24 02:00:00', self.user_new_york)
        self.assertFalse(order._is_weekend_order())

    def test_no_timezone_uses_utc(self):
        """Requirements: BR-LAP-004 - no timezone falls back to UTC."""
        order = self._order('2026-10-25 23:59:00', self.user_no_tz)
        self.assertTrue(order._is_weekend_order())

    def test_monday_is_not_weekend(self):
        """Requirements: BR-LAP-001 - Monday 00:00 is a weekday."""
        order = self._order('2026-10-26 00:00:00', self.user_no_tz)
        self.assertFalse(order._is_weekend_order())

    def test_multiplied_points_round_down(self):
        """Requirements: FR-LAP-006 - 10.99 x 1.15 rounds down to 12.63."""
        self.program.weekend_multiplier = 1.15
        self.assertEqual(self.program._apply_weekend_multiplier(10.99), 12.63)
