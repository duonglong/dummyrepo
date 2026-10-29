from freezegun import freeze_time

from odoo import Command

from .common import WeekendMultiplierCase

# 2026-10-23 is a Friday, 2026-10-24 a Saturday, 2026-10-25 a Sunday and
# 2026-10-26 a Monday.


class TestWeekendConfirmAndScopeAcceptance(WeekendMultiplierCase):
    """Acceptance tests of T-004: confirmation recompute, other program types, reward costs.

    Requirements: BR-LAP-005, FR-LAP-009, FR-LAP-010, FR-LAP-012
    """

    def _program(self, multiplier, **vals):
        values = {
            'name': 'Weekend scope %s' % multiplier,
            'program_type': 'loyalty',
            'trigger': 'auto',
            'applies_on': 'both',
            'weekend_multiplier': multiplier,
            'rule_ids': [Command.clear(), Command.create({
                'reward_point_mode': 'money',
                'reward_point_amount': 1,
            })],
        }
        values.update(vals)
        return self.env['loyalty.program'].create(values)

    def _order(self, date_order, price=100.0, product=None, user=None):
        user = user or self.user_no_tz
        return self.env['sale.order'].with_user(user).create({
            'partner_id': self.partner.id,
            'date_order': date_order,
            'order_line': [Command.create({
                'product_id': (product or self.product).id,
                'product_uom_qty': 1,
                'price_unit': price,
                'tax_ids': [Command.clear()],
            })],
        })

    def _card_points(self, program):
        self.env.flush_all()
        card = self.env['loyalty.card'].search([
            ('program_id', '=', program.id), ('partner_id', '=', self.partner.id)])
        self.assertEqual(len(card), 1)
        return card.points

    def _order_points(self, order, program):
        return sum(order.coupon_point_ids.filtered(
            lambda p: p.coupon_id.program_id == program).mapped('points'))

    def _reward_program(self, multiplier):
        return self._program(multiplier, reward_ids=[Command.create({
            'reward_type': 'discount',
            'discount_mode': 'percent',
            'discount': 10,
            'discount_applicability': 'order',
            'required_points': 100,
        })])

    def _card(self, program, points):
        card = self.env['loyalty.card'].create({
            'program_id': program.id,
            'partner_id': self.partner.id,
        })
        card._adjust_points(points, 'Initial balance')
        return card

    def test_friday_quotation_confirmed_saturday(self):
        """Requirements: FR-LAP-009 - SC-LAP-009-1: Friday quotation confirmed Saturday gives 200."""
        program = self._program(2.0)
        with freeze_time('2026-10-23 12:00:00'):
            order = self._order('2026-10-23 12:00:00')
            order._update_programs_and_rewards()
            self.assertAlmostEqual(self._order_points(order, program), 100.0, places=2)
        with freeze_time('2026-10-24 12:00:00'):
            order.action_confirm()
        self.assertAlmostEqual(self._card_points(program), 200.0, places=2)

    def test_sunday_quotation_confirmed_monday(self):
        """Requirements: FR-LAP-009 - SC-LAP-009-2: Sunday quotation confirmed Monday gives 100."""
        program = self._program(2.0)
        with freeze_time('2026-10-25 12:00:00'):
            order = self._order('2026-10-25 12:00:00')
            order._update_programs_and_rewards()
            self.assertAlmostEqual(self._order_points(order, program), 200.0, places=2)
        with freeze_time('2026-10-26 12:00:00'):
            order.action_confirm()
        self.assertAlmostEqual(self._card_points(program), 100.0, places=2)

    def test_promotion_not_multiplied(self):
        """Requirements: FR-LAP-010 - SC-LAP-010-1: a Promotions program counts 1 point on Sunday."""
        program = self.env['loyalty.program'].create({
            'name': 'Weekend Promotion',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'weekend_multiplier': 3.0,
            'rule_ids': [Command.clear(), Command.create({
                'reward_point_mode': 'order',
                'reward_point_amount': 1,
            })],
            'reward_ids': [Command.create({
                'reward_type': 'discount',
                'discount_mode': 'percent',
                'discount': 10,
                'discount_applicability': 'order',
            })],
        })
        order = self._order('2026-10-25 12:00:00')
        order._update_programs_and_rewards()
        self.env.flush_all()
        self.assertAlmostEqual(self._order_points(order, program), 1.0, places=2)

    def test_ewallet_topup_not_multiplied(self):
        """Requirements: FR-LAP-010 - SC-LAP-010-2: a 50.00 top-up on Saturday gives the eWallet 50."""
        topup = self.env.ref('loyalty.ewallet_product_50')
        program = self.env['loyalty.program'].create({
            'name': 'Weekend eWallet',
            'program_type': 'ewallet',
            'trigger': 'auto',
            'applies_on': 'future',
            'weekend_multiplier': 3.0,
            'reward_ids': [Command.create({
                'reward_type': 'discount',
                'discount_mode': 'per_point',
                'discount': 1,
            })],
            'rule_ids': [Command.clear(), Command.create({
                'reward_point_amount': 1,
                'reward_point_mode': 'money',
                'product_ids': topup,
            })],
        })
        with freeze_time('2026-10-24 12:00:00'):
            order = self._order('2026-10-24 12:00:00', price=50.0, product=topup)
            order.action_confirm()
        self.assertAlmostEqual(self._card_points(program), 50.0, places=2)

    def test_reward_cost_unchanged_on_sunday(self):
        """Requirements: FR-LAP-012, BR-LAP-005 - SC-LAP-012-1: a 100-point reward costs 100 on Sunday."""
        program = self._reward_program(2.0)
        card = self._card(program, 150)
        order = self._order('2026-10-25 12:00:00', price=5.0)
        order._update_programs_and_rewards()
        reward = program.reward_ids
        self.assertIn(reward, order._get_claimable_rewards().get(card, reward.browse()))
        status = order._apply_program_reward(reward, card)
        self.assertNotIn('error', status)
        lines = order.order_line.filtered(lambda l: l.reward_id == reward)
        self.assertTrue(lines)
        self.assertEqual(sum(lines.mapped('points_cost')), 100)

    def test_reward_unavailable_without_points_on_saturday(self):
        """Requirements: FR-LAP-012 - SC-LAP-012-2: 80 points cannot claim a 100-point reward on Saturday."""
        program = self._reward_program(2.0)
        card = self._card(program, 80)
        # The small order earns 10 weekend points: 80 + 10 stays below 100.
        order = self._order('2026-10-24 12:00:00', price=5.0)
        order._update_programs_and_rewards()
        reward = program.reward_ids
        self.assertNotIn(reward, order._get_claimable_rewards().get(card, reward.browse()))
        status = order._apply_program_reward(reward, card)
        self.assertIn('error', status)
