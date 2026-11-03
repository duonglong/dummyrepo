from freezegun import freeze_time

from odoo import Command
from odoo.tests import tagged

from odoo.addons.sale.tests.common import SaleCommon

from .weekend_sale_helpers import WeekendSaleMixin


@tagged('post_install', '-at_install')
class TestSaleOrderPoints(WeekendSaleMixin, SaleCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['loyalty.program'].search([]).sudo().write({'active': False})

    def setUp(self):
        super().setUp()
        self._set_company_tz()

    def _order(self, utc_dt, **vals):
        with freeze_time(utc_dt):
            return self.env['sale.order'].create({
                'partner_id': self.partner.id,
                'date_order': utc_dt,
                'order_line': [Command.create({
                    'product_id': self.product.id, 'product_uom_qty': 1, 'price_unit': 100,
                })],
                **vals,
            })

    def test_weekday_earns_normal_points(self):
        """Requirements: FR-DLP-002; a weekday order earns normal points."""
        program = self._make_program(mode='order', amount=10)
        self._confirm_order_at(self.WED_LOCAL)
        self.assertEqual(self._card_points(program), 10)

    def test_option_off_earns_normal_points(self):
        """Requirements: FR-DLP-002; a program without the option is not doubled."""
        program = self._make_program(weekend=False, mode='order', amount=10)
        self._confirm_order_at(self.SAT_10H_LOCAL)
        self.assertEqual(self._card_points(program), 10)

    def test_friday_2330_and_monday_midnight_normal(self):
        """Requirements: FR-DLP-002, NFR-REL-001; just outside the weekend."""
        program = self._make_program(mode='order', amount=10)
        self._confirm_order_at(self.FRI_2330_LOCAL)
        self.assertEqual(self._card_points(program), 10)
        self._confirm_order_at(self.MON_0000_LOCAL)
        self.assertEqual(self._card_points(program), 20)

    def test_unconfirmed_quotation_uses_now(self):
        """Requirements: FR-DLP-002; a draft quotation on Saturday shows doubled points."""
        program = self._make_program(mode='order', amount=10)
        order = self._order(self.WED_LOCAL)
        with freeze_time(self.SAT_10H_LOCAL):
            status = order._program_check_compute_points(program)
        self.assertEqual(status[program]['points'], [20])

    def test_payment_before_midnight_confirmed_after(self):
        """Requirements: FR-DLP-002, NFR-REL-001; paid Sunday 23:59, confirmed Monday 00:05."""
        program = self._make_program(mode='order', amount=10)
        order = self._order(self.SUN_2359_LOCAL)
        provider = self.env['payment.provider'].sudo().search([], limit=1) or \
            self.env['payment.provider'].sudo().create({'name': 'Test'})
        method = self.env['payment.method'].sudo().search([], limit=1) or \
            self.env['payment.method'].sudo().create({'name': 'Test', 'code': 'testm', 'provider_id': provider.id})
        with freeze_time(self.SUN_2359_LOCAL):
            tx = self.env['payment.transaction'].sudo().create({
                'provider_id': provider.id,
                'payment_method_id': method.id,
                'amount': 100,
                'currency_id': order.currency_id.id,
                'partner_id': self.partner.id,
                'reference': 'TX-WEEKEND',
                'sale_order_ids': [Command.link(order.id)],
                'state': 'done',
            })
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE payment_transaction SET create_date = %s WHERE id = %s",
            (self.SUN_2359_LOCAL, tx.id))
        tx.invalidate_recordset()
        order.invalidate_recordset()
        with freeze_time(self.MON_0000_LOCAL):
            order.write({'date_order': '2026-11-08 23:05:00'})
            order.action_confirm()
        self.assertEqual(self._card_points(program), 20)
