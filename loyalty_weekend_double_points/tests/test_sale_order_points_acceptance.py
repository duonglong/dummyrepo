from odoo import Command
from odoo.tests import tagged

from odoo.addons.sale.tests.common import SaleCommon

from .weekend_sale_helpers import WeekendSaleMixin


@tagged('post_install', '-at_install')
class TestSaleOrderPointsAcceptance(WeekendSaleMixin, SaleCommon):
    """Acceptance tests of T-003: double points on weekend sales orders."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['loyalty.program'].search([]).sudo().write({'active': False})

    def setUp(self):
        super().setUp()
        self._set_company_tz()

    def test_saturday_order_per_money_earns_double(self):
        """Requirements: FR-DLP-002, BR-DLP-002; scenario SC-DLP-002-1."""
        program = self._make_program(mode='money', amount=1)
        self._confirm_order_at(self.SAT_10H_LOCAL, price=100.0)
        self.assertEqual(self._card_points(program), 200)

    def test_sunday_2359_local_order_per_order_earns_double(self):
        """Requirements: FR-DLP-002, NFR-REL-001; scenario SC-DLP-002-2 (last minute of the weekend)."""
        program = self._make_program(mode='order', amount=10)
        self._confirm_order_at(self.SUN_2359_LOCAL, price=100.0)
        self.assertEqual(self._card_points(program), 20)

    def test_saturday_0030_local_is_weekend_even_if_friday_in_utc(self):
        """Requirements: FR-DLP-002, BR-DLP-001; boundary just inside the weekend."""
        program = self._make_program(mode='order', amount=10)
        self._confirm_order_at(self.SAT_0030_LOCAL, price=100.0)
        self.assertEqual(self._card_points(program), 20)

    def test_saturday_per_unit_earns_double(self):
        """Requirements: FR-DLP-002; scenario SC-DLP-002-3."""
        program = self._make_program(mode='unit', amount=2, product=self.product)
        self._confirm_order_at(self.SAT_10H_LOCAL, price=10.0, qty=3.0)
        self.assertEqual(self._card_points(program), 12)

    def test_redeeming_reward_costs_normal_points(self):
        """Requirements: FR-DLP-005; scenario SC-DLP-005-1."""
        program = self._make_program(mode='money', amount=1, with_reward=True)
        card = self.env['loyalty.card'].sudo().create({
            'program_id': program.id, 'partner_id': self.partner.id})
        card._adjust_points(300, 'Initial balance')
        self.assertEqual(card.points, 300)
        self._confirm_order_at(
            self.SAT_10H_LOCAL, price=50.0, claim=(program.reward_ids[0], card))
        # -100 for the reward, +100 earned (50 doubled): not -200.
        self.assertEqual(card.points, 300)
