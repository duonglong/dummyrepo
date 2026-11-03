from odoo import Command
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon


@tagged('post_install', '-at_install')
class TestPosScreenData(TestPointOfSaleHttpCommon):
    """Data the point of sale screen needs to double the points."""

    def test_company_timezone_is_loaded(self):
        """Requirements: FR-DLP-007, NFR-REL-001: the screen decides the weekend in the company timezone."""
        self.main_pos_config.company_id.tz = 'Europe/Brussels'
        self.main_pos_config.with_user(self.pos_user).open_ui()
        session = self.main_pos_config.current_session_id
        data = session.load_data({'only_records': True})
        company = data['res.company'][0]
        self.assertEqual(company['tz'], 'Europe/Brussels')


@tagged('post_install', '-at_install')
class TestPosScreenPaymentMoment(TestPointOfSaleHttpCommon):
    """The screen decides the weekend from the moment the order will be paid."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['loyalty.program'].search([]).write({'active': False})
        cls.env['res.partner'].create({'name': 'Weekend screen customer'})

    def setUp(self):
        super().setUp()
        self.main_pos_config.company_id.tz = 'Europe/Brussels'
        self.env['loyalty.program'].create({
            'name': 'Weekend midnight program',
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

    def test_bug_b_0069_order_opened_friday_shows_double_after_midnight(self):
        """Regression test for B-0069: the screen reads the opening moment, not the payment moment.

        Requirements: FR-DLP-007, NFR-REL-001, BR-DLP-001; scenario SC-DLP-003-2.
        Opened Friday 23:50 company time, read Saturday 00:10: the screen shows 80 points.
        """
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour('WeekendPointsMidnightTour')
