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

    def test_weekday_and_option_off_earn_normal_points(self):
        """Requirements: FR-DLP-002, FR-DLP-004; weekday or option off keeps normal points."""
        weekday = self._make_program(mode='order', amount=10)
        self._confirm_order_at(self.WED_LOCAL)
        self.assertEqual(self._card_points(weekday), 10)
        weekday.sudo().write({'active': False})
        off = self._make_program(weekend=False, mode='order', amount=10)
        self._confirm_order_at(self.SAT_10H_LOCAL)
        self.assertEqual(self._card_points(off), 10)
