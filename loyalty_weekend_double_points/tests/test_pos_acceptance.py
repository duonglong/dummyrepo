from odoo import Command
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon


@tagged('post_install', '-at_install')
class TestPosOrderPointsAcceptance(TestPointOfSaleHttpCommon):
    """Acceptance tests of T-004: weekend double points in the point of sale and refunds."""

    # Europe/Brussels is UTC+1 in November 2026; 2026-11-07 is a Saturday.
    SAT_UTC = '2026-11-07 10:00:00'
    SUN_UTC = '2026-11-08 12:00:00'
    WED_UTC = '2026-11-04 11:00:00'
    MON_UTC = '2026-11-09 11:00:00'
    TUE_UTC = '2026-11-10 11:00:00'
    SAT_0030_LOCAL = '2026-11-06 23:30:00'  # Friday 23:30 UTC
    FRI_2330_LOCAL = '2026-11-06 22:30:00'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['loyalty.program'].search([]).write({'active': False})
        cls.partner_pos = cls.env['res.partner'].create({'name': 'Weekend POS customer'})
        cls.pen = cls.whiteboard_pen.product_variant_ids[:1]

    def setUp(self):
        super().setUp()
        self.main_pos_config.company_id.tz = 'Europe/Brussels'
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.session = self.main_pos_config.current_session_id
        self.program = self.env['loyalty.program'].create({
            'name': 'Weekend POS program',
            'program_type': 'loyalty',
            'trigger': 'auto',
            'applies_on': 'both',
            'weekend_double_points': True,
            'rule_ids': [Command.create({
                'reward_point_mode': 'money', 'reward_point_amount': 1})],
            'reward_ids': [Command.create({
                'reward_type': 'discount', 'discount_mode': 'percent', 'discount': 10,
                'discount_applicability': 'order', 'required_points': 1000000})],
            'pos_config_ids': [Command.link(self.main_pos_config.id)],
        })

    def _card(self):
        return self.env['loyalty.card'].search([
            ('program_id', '=', self.program.id),
            ('partner_id', '=', self.partner_pos.id)])

    def _pay(self, date_order, qty=1, price=50.0, refunded_line=None):
        sign = -1 if refunded_line else 1
        total = sign * qty * price
        line = {
            'product_id': self.pen.id,
            'price_unit': price,
            'qty': sign * qty,
            'price_subtotal': total,
            'price_subtotal_incl': total,
        }
        if refunded_line:
            line['refunded_orderline_id'] = refunded_line.id
        order = self.env['pos.order'].create({
            'config_id': self.main_pos_config.id,
            'session_id': self.session.id,
            'partner_id': self.partner_pos.id,
            'date_order': date_order,
            'is_refund': bool(refunded_line),
            'lines': [Command.create(line)],
            'amount_paid': total,
            'amount_return': 0,
            'amount_tax': 0,
            'amount_total': total,
        })
        order._process_loyalty()
        self.env.flush_all()
        return order

    def test_saturday_order_earns_double(self):
        """Requirements: FR-DLP-003, BR-DLP-002; scenario SC-DLP-003-1."""
        self._pay(self.SAT_UTC, price=50.0)
        self.assertEqual(self._card().points, 100)

    def test_saturday_0030_local_is_weekend_though_friday_in_utc(self):
        """Requirements: FR-DLP-003, BR-DLP-001; scenario SC-DLP-003-2."""
        self._pay(self.SAT_0030_LOCAL, price=50.0)
        self.assertEqual(self._card().points, 100)

    def test_friday_2330_local_earns_normal_points(self):
        """Requirements: FR-DLP-004, BR-DLP-001; boundary just below the weekend."""
        self._pay(self.FRI_2330_LOCAL, price=50.0)
        self.assertEqual(self._card().points, 50)

    def test_wednesday_order_earns_normal_points(self):
        """Requirements: FR-DLP-003, NFR-REL-001; weekday in the point of sale."""
        self._pay(self.WED_UTC, price=50.0)
        self.assertEqual(self._card().points, 50)

    def test_refund_on_monday_of_saturday_order_takes_back_doubled_points(self):
        """Requirements: FR-DLP-006; scenario SC-DLP-006-1."""
        origin = self._pay(self.SAT_UTC, price=50.0)
        self.assertEqual(self._card().points, 100)
        self._pay(self.MON_UTC, price=50.0, refunded_line=origin.lines[0])
        self.assertEqual(self._card().points, 0)

    def test_refund_on_saturday_of_wednesday_order_takes_back_normal_points(self):
        """Requirements: FR-DLP-006; scenario SC-DLP-006-2."""
        origin = self._pay(self.WED_UTC, price=50.0)
        self.assertEqual(self._card().points, 50)
        self._pay(self.SAT_UTC, price=50.0, refunded_line=origin.lines[0])
        self.assertEqual(self._card().points, 0)

    def test_partial_refund_takes_back_proportional_doubled_points(self):
        """Requirements: FR-DLP-006; scenario SC-DLP-006-3."""
        origin = self._pay(self.SUN_UTC, qty=2, price=25.0)
        self.assertEqual(self._card().points, 100)
        self._pay(self.TUE_UTC, qty=1, price=25.0, refunded_line=origin.lines[0])
        self.assertEqual(self._card().points, 50)
