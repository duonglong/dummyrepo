from freezegun import freeze_time

from odoo import Command

from .common import WeekendMultiplierCase

# 2026-10-21 is a Wednesday, 2026-10-23 a Friday, 2026-10-24 a Saturday,
# 2026-10-25 a Sunday and 2026-10-26 a Monday.


class TestWeekendPointsSaleAcceptance(WeekendMultiplierCase):
    """Acceptance tests of T-003: weekend points on sales orders.

    Requirements: BR-LAP-001, BR-LAP-003, BR-LAP-004, FR-LAP-003,
    FR-LAP-004, FR-LAP-005, FR-LAP-006, FR-LAP-007
    """

    def _program(self, multiplier, mode='money', amount=1):
        return self.env['loyalty.program'].create({
            'name': 'Weekend %s %s' % (mode, multiplier),
            'program_type': 'loyalty',
            'trigger': 'auto',
            'applies_on': 'both',
            'weekend_multiplier': multiplier,
            'rule_ids': [Command.clear(), Command.create({
                'reward_point_mode': mode,
                'reward_point_amount': amount,
            })],
        })

    def _order(self, date_order, price=100.0, qty=1, user=None):
        user = user or self.user_no_tz
        return self.env['sale.order'].with_user(user).create({
            'partner_id': self.partner.id,
            'date_order': date_order,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'product_uom_qty': qty,
                'price_unit': price,
                'tax_ids': [Command.clear()],
            })],
        })

    def _points(self, order, program):
        order._update_programs_and_rewards()
        self.env.flush_all()
        entries = order.coupon_point_ids.filtered(
            lambda p: p.coupon_id.program_id == program)
        return sum(entries.mapped('points'))

    def test_saturday_money_rule_doubles(self):
        """Requirements: FR-LAP-003 - SC-LAP-003-1: 100.00 on Saturday, x2 gives 200."""
        program = self._program(2.0)
        order = self._order('2026-10-24 12:00:00')
        self.assertAlmostEqual(self._points(order, program), 200.0, places=2)

    def test_sunday_per_order_triples(self):
        """Requirements: FR-LAP-003 - SC-LAP-003-2: 10 points per order on Sunday, x3 gives 30."""
        program = self._program(3.0, mode='order', amount=10)
        order = self._order('2026-10-25 12:00:00')
        self.assertAlmostEqual(self._points(order, program), 30.0, places=2)

    def test_saturday_per_unit_neutral(self):
        """Requirements: FR-LAP-003 - SC-LAP-003-3: 4 units, 1 point per unit, x1 gives 4."""
        program = self._program(1.0, mode='unit', amount=1)
        order = self._order('2026-10-24 12:00:00', qty=4)
        self.assertAlmostEqual(self._points(order, program), 4.0, places=2)

    def test_per_unit_weekend_multiplied(self):
        """Requirements: BR-LAP-003, FR-LAP-003 - per unit mode is multiplied too: 4 units x2 gives 8."""
        program = self._program(2.0, mode='unit', amount=1)
        order = self._order('2026-10-24 12:00:00', qty=4)
        self.assertAlmostEqual(self._points(order, program), 8.0, places=2)

    def test_wednesday_native_points(self):
        """Requirements: FR-LAP-004 - SC-LAP-004-1: Wednesday earns 100, no multiplier."""
        program = self._program(2.0)
        order = self._order('2026-10-21 12:00:00')
        self.assertAlmostEqual(self._points(order, program), 100.0, places=2)

    def test_friday_2359_is_weekday(self):
        """Requirements: FR-LAP-004 - SC-LAP-004-2: Friday 23:59 earns 100."""
        program = self._program(2.0)
        order = self._order('2026-10-23 23:59:00')
        self.assertAlmostEqual(self._points(order, program), 100.0, places=2)

    def test_monday_0000_is_weekday(self):
        """Requirements: FR-LAP-004 - SC-LAP-004-3: Monday 00:00 earns 100."""
        program = self._program(2.0)
        order = self._order('2026-10-26 00:00:00')
        self.assertAlmostEqual(self._points(order, program), 100.0, places=2)

    def test_saturday_0000_is_weekend(self):
        """Requirements: BR-LAP-001, FR-LAP-003 - Saturday 00:00 is the first weekend instant: 200."""
        program = self._program(2.0)
        order = self._order('2026-10-24 00:00:00')
        self.assertAlmostEqual(self._points(order, program), 200.0, places=2)

    def test_sunday_2359_is_weekend(self):
        """Requirements: BR-LAP-001, FR-LAP-003 - Sunday 23:59 is still weekend: 200."""
        program = self._program(2.0)
        order = self._order('2026-10-25 23:59:00')
        self.assertAlmostEqual(self._points(order, program), 200.0, places=2)

    def test_brussels_friday_utc_is_saturday(self):
        """Requirements: FR-LAP-005 - SC-LAP-005-1: Fri 23:30 UTC is Saturday in Brussels: 200."""
        program = self._program(2.0)
        order = self._order('2026-10-23 23:30:00', user=self.user_brussels)
        self.assertAlmostEqual(self._points(order, program), 200.0, places=2)

    def test_new_york_saturday_utc_is_friday(self):
        """Requirements: FR-LAP-005 - SC-LAP-005-2: Sat 02:00 UTC is Friday in New York: 100."""
        program = self._program(2.0)
        order = self._order('2026-10-24 02:00:00', user=self.user_new_york)
        self.assertAlmostEqual(self._points(order, program), 100.0, places=2)

    def test_no_timezone_uses_utc(self):
        """Requirements: FR-LAP-005 - SC-LAP-005-3: no timezone, Sun 23:59 UTC is Sunday: 200."""
        program = self._program(2.0)
        order = self._order('2026-10-25 23:59:00', user=self.user_no_tz)
        self.assertAlmostEqual(self._points(order, program), 200.0, places=2)

    def test_rounding_down_to_two_decimals(self):
        """Requirements: FR-LAP-006 - SC-LAP-006-1: 10.99 x 1.15 = 12.6385 gives 12.63."""
        program = self._program(1.15)
        order = self._order('2026-10-24 12:00:00', price=10.99)
        self.assertAlmostEqual(self._points(order, program), 12.63, places=2)

    def test_fractional_multiplier_per_order(self):
        """Requirements: FR-LAP-006 - SC-LAP-006-2: 1 point per order x1.50 gives 1.50."""
        program = self._program(1.5, mode='order', amount=1)
        order = self._order('2026-10-25 12:00:00')
        self.assertAlmostEqual(self._points(order, program), 1.5, places=2)

    def test_confirmed_saturday_order_credits_card(self):
        """Requirements: FR-LAP-007 - SC-LAP-007-1: confirming on Saturday credits 200 to the card."""
        program = self._program(2.0)
        with freeze_time('2026-10-24 12:00:00'):
            order = self._order('2026-10-24 12:00:00')
            order.action_confirm()
        self.env.flush_all()
        card = self.env['loyalty.card'].search([
            ('program_id', '=', program.id), ('partner_id', '=', self.partner.id)])
        self.assertEqual(len(card), 1)
        self.assertAlmostEqual(card.points, 200.0, places=2)

    def test_draft_sunday_quotation_shows_multiplied_points(self):
        """Requirements: FR-LAP-007 - SC-LAP-007-2: a Sunday draft shows 200 points to earn."""
        program = self._program(2.0)
        order = self._order('2026-10-25 12:00:00')
        self.assertEqual(order.state, 'draft')
        self.assertAlmostEqual(self._points(order, program), 200.0, places=2)
