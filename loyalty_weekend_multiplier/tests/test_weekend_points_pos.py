from freezegun import freeze_time

from odoo import Command
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.common import CommonPosTest
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon

# 2026-10-21 is a Wednesday, 2026-10-24 a Saturday, 2026-10-25 a Sunday and
# 2026-10-26 a Monday.


def _make_program(env, **vals):
    return env['loyalty.program'].create({
        'name': 'Weekend POS Loyalty',
        'program_type': 'loyalty',
        'trigger': 'auto',
        'applies_on': 'both',
        'weekend_multiplier': 2.0,
        'rule_ids': [Command.clear(), Command.create({
            'reward_point_mode': 'money',
            'reward_point_amount': 1,
        })],
        **vals,
    })


@tagged('post_install', '-at_install')
class TestWeekendPointsPos(CommonPosTest):
    """Point of Sale points, refunds and reward cost with a Weekend Multiplier."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.tz = 'UTC'
        cls.partner = cls.env['res.partner'].create({'name': 'Weekend POS Customer'})
        cls.product = cls.env['product.product'].create({
            'name': 'Weekend POS Product',
            'available_in_pos': True,
            'list_price': 100.0,
            'taxes_id': [Command.clear()],
        })
        cls.program = _make_program(cls.env, reward_ids=[Command.create({
            'reward_type': 'discount',
            'discount_mode': 'percent',
            'discount': 10,
            'required_points': 100,
            'discount_applicability': 'order',
        })])
        cls.config = cls.pos_config_usd
        if not cls.config.current_session_id:
            cls.config.open_ui()

    def _order(self, date_order, extra_lines=()):
        return self.env['pos.order'].create({
            'config_id': self.config.id,
            'session_id': self.config.current_session_id.id,
            'partner_id': self.partner.id,
            'date_order': date_order,
            'amount_total': 100.0,
            'amount_paid': 100.0,
            'amount_tax': 0.0,
            'amount_return': 0.0,
            'lines': [Command.create({
                'product_id': self.product.id,
                'qty': 1,
                'price_unit': 100.0,
                'price_subtotal': 100.0,
                'price_subtotal_incl': 100.0,
                'tax_ids': [Command.clear()],
            })] + [Command.create(vals) for vals in extra_lines],
        })

    def _points(self):
        self.env.flush_all()
        return sum(self.env['loyalty.card'].search([
            ('program_id', '=', self.program.id),
            ('partner_id', '=', self.partner.id),
        ]).mapped('points'))

    def _refund(self, order, when):
        with freeze_time(when):
            refund = order._refund()
            refund._process_loyalty()

    def test_pos_saturday_validate_200(self):
        """Requirements: FR-LAP-008 - a 100.00 Saturday order saves 200 points."""
        self._order('2026-10-24 12:00:00')._process_loyalty()
        self.assertAlmostEqual(self._points(), 200.0, places=2)

    def test_pos_tuesday_validate_100(self):
        """Requirements: FR-LAP-008 - a 100.00 Tuesday order saves 100 points."""
        self._order('2026-10-27 12:00:00')._process_loyalty()
        self.assertAlmostEqual(self._points(), 100.0, places=2)

    def test_refund_monday_of_saturday_removes_200(self):
        """Requirements: FR-LAP-011 - a Saturday order refunded Monday removes 200."""
        order = self._order('2026-10-24 12:00:00')
        order._process_loyalty()
        self._refund(order, '2026-10-26 12:00:00')
        self.assertAlmostEqual(self._points(), 0.0, places=2)

    def test_refund_saturday_of_wednesday_removes_100(self):
        """Requirements: FR-LAP-011 - a Wednesday order refunded Saturday removes 100."""
        order = self._order('2026-10-21 12:00:00')
        order._process_loyalty()
        self._refund(order, '2026-10-24 12:00:00')
        self.assertAlmostEqual(self._points(), 0.0, places=2)

    def test_pos_reward_cost_unchanged(self):
        """Requirements: FR-LAP-008, FR-LAP-012 - a reward claimed on a Sunday costs 100, not 200.

        The card first holds 100 points from a Tuesday order. A Sunday order
        then claims the 100-point reward: the native cost path
        (loyalty.reward._get_pos_points_cost) charges 100 while the points the
        order earns are still multiplied.
        """
        self._order('2026-10-27 12:00:00')._process_loyalty()
        card = self.env['loyalty.card'].search([
            ('program_id', '=', self.program.id),
            ('partner_id', '=', self.partner.id),
        ])
        self.assertAlmostEqual(card.points, 100.0, places=2)
        reward = self.program.reward_ids
        sunday = self._order('2026-10-25 12:00:00', extra_lines=[{
            'product_id': reward.discount_line_product_id.id,
            'qty': 1,
            'price_unit': -10.0,
            'price_subtotal': -10.0,
            'price_subtotal_incl': -10.0,
            'tax_ids': [Command.clear()],
            'is_reward_line': True,
            'reward_id': reward.id,
            'card_id': card.id,
        }])
        sunday._process_loyalty()
        reward_line = sunday.lines.filtered('is_reward_line')
        self.assertAlmostEqual(reward_line.points_cost, 100.0, places=2)
        history = self.env['loyalty.history'].search([
            ('order_model', '=', 'pos.order'),
            ('order_id', '=', sunday.id),
            ('card_id', '=', card.id),
        ])
        self.assertAlmostEqual(sum(history.mapped('used')), 100.0, places=2)
        self.assertAlmostEqual(sum(history.mapped('issued')), 200.0, places=2)
        self.assertAlmostEqual(self._points(), 200.0, places=2)


@tagged('post_install', '-at_install')
class TestWeekendPointsPosTour(TestPointOfSaleHttpCommon):
    """Cashier view of the points to earn on a Saturday order."""

    def test_pos_ui_shows_200_for_saturday(self):
        """Requirements: FR-LAP-008 - the point of sale shows 200.00 points to earn."""
        self.env['res.partner'].create({'name': 'Weekend POS Customer'})
        self.env['product.template'].create({
            'name': 'Weekend POS Product',
            'available_in_pos': True,
            'list_price': 100.0,
            'taxes_id': [Command.clear()],
        })
        _make_program(self.env)
        self.main_pos_config.write({'tax_regime_selection': False, 'use_pricelist': False})
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour('pos_weekend_points_tour')
