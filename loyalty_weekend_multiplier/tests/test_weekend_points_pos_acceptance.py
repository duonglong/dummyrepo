from freezegun import freeze_time

from odoo import Command
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.common import CommonPosTest

# 2026-10-21 is a Wednesday, 2026-10-24 a Saturday, 2026-10-26 a Monday and
# 2026-10-27 a Tuesday.


@tagged('post_install', '-at_install')
class TestWeekendPointsPosAcceptance(CommonPosTest):
    """Acceptance tests of T-005: weekend points and refunds in Point of Sale.

    Requirements: FR-LAP-008, FR-LAP-011
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.tz = 'UTC'
        cls.lwm_partner = cls.env['res.partner'].create({'name': 'Weekend POS Customer'})
        cls.lwm_product = cls.env['product.product'].create({
            'name': 'Weekend POS Product',
            'available_in_pos': True,
            'list_price': 100.0,
            'taxes_id': [Command.clear()],
        })
        cls.lwm_program = cls.env['loyalty.program'].create({
            'name': 'Weekend POS Loyalty',
            'program_type': 'loyalty',
            'trigger': 'auto',
            'applies_on': 'both',
            'weekend_multiplier': 2.0,
            'rule_ids': [Command.clear(), Command.create({
                'reward_point_mode': 'money',
                'reward_point_amount': 1,
            })],
        })
        cls.lwm_config = cls.pos_config_usd
        if not cls.lwm_config.current_session_id:
            cls.lwm_config.open_ui()

    def _pos_order(self, date_order):
        return self.env['pos.order'].create({
            'config_id': self.lwm_config.id,
            'session_id': self.lwm_config.current_session_id.id,
            'partner_id': self.lwm_partner.id,
            'date_order': date_order,
            'amount_total': 100.0,
            'amount_paid': 100.0,
            'amount_tax': 0.0,
            'amount_return': 0.0,
            'lines': [Command.create({
                'product_id': self.lwm_product.id,
                'qty': 1,
                'price_unit': 100.0,
                'price_subtotal': 100.0,
                'price_subtotal_incl': 100.0,
                'tax_ids': [Command.clear()],
            })],
        })

    def _card_points(self):
        self.env.flush_all()
        cards = self.env['loyalty.card'].search([
            ('program_id', '=', self.lwm_program.id),
            ('partner_id', '=', self.lwm_partner.id),
        ])
        return sum(cards.mapped('points'))

    def _refund(self, order, when):
        with freeze_time(when):
            refund = order._refund()
            refund._process_loyalty()
        return refund

    def test_saturday_order_card_receives_double(self):
        """Requirements: FR-LAP-008 - SC-LAP-008-2: 100.00 on Saturday, x2 saves 200 on the card."""
        order = self._pos_order('2026-10-24 12:00:00')
        order._process_loyalty()
        self.assertAlmostEqual(self._card_points(), 200.0, places=2)

    def test_tuesday_order_card_receives_native(self):
        """Requirements: FR-LAP-008 - SC-LAP-008-3: 100.00 on Tuesday saves 100 on the card."""
        order = self._pos_order('2026-10-27 12:00:00')
        order._process_loyalty()
        self.assertAlmostEqual(self._card_points(), 100.0, places=2)

    def test_multiplier_reaches_point_of_sale_data(self):
        """Requirements: FR-LAP-008 - SC-LAP-008-1: the point of sale loads the multiplier to show 200.00 points."""
        fields_loaded = self.env['loyalty.program']._load_pos_data_fields(self.lwm_config)
        self.assertIn('weekend_multiplier', fields_loaded)

    def test_saturday_order_refunded_monday_removes_double(self):
        """Requirements: FR-LAP-011 - SC-LAP-011-1: Saturday 200 points refunded Monday removes 200."""
        order = self._pos_order('2026-10-24 12:00:00')
        order._process_loyalty()
        self.assertAlmostEqual(self._card_points(), 200.0, places=2)
        self._refund(order, '2026-10-26 12:00:00')
        self.assertAlmostEqual(self._card_points(), 0.0, places=2)

    def test_wednesday_order_refunded_saturday_removes_native(self):
        """Requirements: FR-LAP-011 - SC-LAP-011-2: Wednesday 100 points refunded Saturday removes 100."""
        order = self._pos_order('2026-10-21 12:00:00')
        order._process_loyalty()
        self.assertAlmostEqual(self._card_points(), 100.0, places=2)
        self._refund(order, '2026-10-24 12:00:00')
        self.assertAlmostEqual(self._card_points(), 0.0, places=2)

    def test_saturday_refund_of_saturday_order_is_symmetric(self):
        """Requirements: FR-LAP-011 - Saturday order refunded on a Saturday removes the 200 it gave."""
        order = self._pos_order('2026-10-24 12:00:00')
        order._process_loyalty()
        self._refund(order, '2026-10-24 18:00:00')
        self.assertAlmostEqual(self._card_points(), 0.0, places=2)
