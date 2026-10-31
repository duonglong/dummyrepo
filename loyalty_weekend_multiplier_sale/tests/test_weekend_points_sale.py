from datetime import datetime

from odoo import Command

from .common import WeekendMultiplierCase

# 2026-10-21 is a Wednesday, 2026-10-23 a Friday, 2026-10-24 a Saturday,
# 2026-10-25 a Sunday and 2026-10-26 a Monday.


class TestWeekendPointsSale(WeekendMultiplierCase):
    """Unit tests of the weekend day and multiplier helpers of loyalty.program
    and of their use on sales orders.

    Requirements: BR-LAP-001, BR-LAP-003, BR-LAP-004, FR-LAP-003, FR-LAP-004,
    FR-LAP-005, FR-LAP-006, FR-LAP-007
    """

    def _make_program(self, multiplier, mode='money', amount=1):
        return self.env['loyalty.program'].create({
            'name': 'Unit %s %s' % (mode, multiplier),
            'program_type': 'loyalty',
            'trigger': 'auto',
            'applies_on': 'both',
            'weekend_multiplier': multiplier,
            'rule_ids': [Command.clear(), Command.create({
                'reward_point_mode': mode,
                'reward_point_amount': amount,
            })],
        })

    def _make_order(self, date_order, price=100.0, qty=1, user=None):
        return self.env['sale.order'].with_user(user or self.user_no_tz).create({
            'partner_id': self.partner.id,
            'date_order': date_order,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'product_uom_qty': qty,
                'price_unit': price,
                'tax_ids': [Command.clear()],
            })],
        })

    def _earned(self, order, program):
        order._update_programs_and_rewards()
        self.env.flush_all()
        return sum(order.coupon_point_ids.filtered(
            lambda p: p.coupon_id.program_id == program).mapped('points'))

    # Day helper: loyalty.program._lwm_is_weekend(order_datetime, user)

    def test_tz_brussels_friday_utc_is_saturday(self):
        """Requirements: FR-LAP-005 - SC-LAP-005-1."""
        self.assertTrue(self.env['loyalty.program']._lwm_is_weekend(
            datetime(2026, 10, 23, 23, 30), self.user_brussels))

    def test_tz_new_york_saturday_utc_is_friday(self):
        """Requirements: FR-LAP-005 - SC-LAP-005-2."""
        self.assertFalse(self.env['loyalty.program']._lwm_is_weekend(
            datetime(2026, 10, 24, 2, 0), self.user_new_york))

    def test_no_tz_sunday_utc(self):
        """Requirements: BR-LAP-004, FR-LAP-005 - SC-LAP-005-3."""
        self.assertTrue(self.env['loyalty.program']._lwm_is_weekend(
            datetime(2026, 10, 25, 23, 59), self.user_no_tz))

    # Multiplier helper: loyalty.program._lwm_apply_multiplier(points)

    def test_rounding_1_15_gives_12_63(self):
        """Requirements: FR-LAP-006 - SC-LAP-006-1 at helper level."""
        program = self._make_program(1.15)
        self.assertEqual(program._lwm_apply_multiplier(10.99), 12.63)
        self.assertEqual(self._make_program(2.0)._lwm_apply_multiplier(100), 200.0)

    def test_per_order_1_5_gives_1_50(self):
        """Requirements: FR-LAP-006 - SC-LAP-006-2."""
        program = self._make_program(1.5, mode='order', amount=1)
        self.assertEqual(program._lwm_apply_multiplier(1), 1.5)
        order = self._make_order('2026-10-25 12:00:00')
        self.assertAlmostEqual(self._earned(order, program), 1.5, places=2)

    # Sales orders

    def test_saturday_money_x2(self):
        """Requirements: FR-LAP-003, FR-LAP-007 - SC-LAP-003-1."""
        program = self._make_program(2.0)
        order = self._make_order('2026-10-24 12:00:00')
        self.assertAlmostEqual(self._earned(order, program), 200.0, places=2)

    def test_sunday_per_order_x3(self):
        """Requirements: FR-LAP-003, BR-LAP-003 - SC-LAP-003-2."""
        program = self._make_program(3.0, mode='order', amount=10)
        order = self._make_order('2026-10-25 12:00:00')
        self.assertAlmostEqual(self._earned(order, program), 30.0, places=2)

    def test_per_unit_x1(self):
        """Requirements: FR-LAP-003, BR-LAP-003 - SC-LAP-003-3."""
        program = self._make_program(1.0, mode='unit', amount=1)
        order = self._make_order('2026-10-24 12:00:00', qty=4)
        self.assertAlmostEqual(self._earned(order, program), 4.0, places=2)

    def test_wednesday_100(self):
        """Requirements: FR-LAP-004 - SC-LAP-004-1."""
        program = self._make_program(2.0)
        order = self._make_order('2026-10-21 12:00:00')
        self.assertAlmostEqual(self._earned(order, program), 100.0, places=2)

    def test_friday_2359_local_100(self):
        """Requirements: FR-LAP-004, FR-LAP-005 - SC-LAP-004-2 (Brussels local)."""
        program = self._make_program(2.0)
        # Friday 23:59 in Brussels (CEST, UTC+2) is Friday 21:59 UTC.
        order = self._make_order('2026-10-23 21:59:00', user=self.user_brussels)
        self.assertAlmostEqual(self._earned(order, program), 100.0, places=2)

    def test_monday_0000_local_100(self):
        """Requirements: FR-LAP-004, FR-LAP-005 - SC-LAP-004-3 (Brussels local)."""
        program = self._make_program(2.0)
        # Monday 00:00 in Brussels (CET, UTC+1) is Sunday 23:00 UTC.
        order = self._make_order('2026-10-25 23:00:00', user=self.user_brussels)
        self.assertAlmostEqual(self._earned(order, program), 100.0, places=2)

    def test_draft_quotation_shows_200(self):
        """Requirements: FR-LAP-007 - SC-LAP-007-2."""
        program = self._make_program(2.0)
        order = self._make_order('2026-10-25 12:00:00')
        self.assertEqual(order.state, 'draft')
        self.assertAlmostEqual(self._earned(order, program), 200.0, places=2)
