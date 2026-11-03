from odoo import Command
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon


@tagged('post_install', '-at_install')
class TestPosScreenAcceptance(TestPointOfSaleHttpCommon):
    """Acceptance tests of T-005: the point of sale screen shows the doubled points."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['loyalty.program'].search([]).write({'active': False})
        cls.partner_screen = cls.env['res.partner'].create({'name': 'Weekend screen customer'})

    def setUp(self):
        super().setUp()
        self.main_pos_config.company_id.tz = 'Europe/Brussels'
        self.program = self.env['loyalty.program'].create({
            'name': 'Weekend screen program',
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

    def _loaded_programs(self):
        self.main_pos_config.with_user(self.pos_user).open_ui()
        session = self.main_pos_config.current_session_id
        data = session.load_data({'only_records': True})
        return {program['id']: program for program in data['loyalty.program']}

    def test_screen_receives_weekend_option_on(self):
        """Requirements: FR-DLP-007, NFR-REL-001; scenario SC-DLP-007-1.

        The screen can only double the figure for a program whose switch it knows is on.
        """
        programs = self._loaded_programs()
        self.assertIn(self.program.id, programs)
        self.assertTrue(programs[self.program.id].get('weekend_double_points'))

    def test_screen_receives_weekend_option_off(self):
        """Requirements: FR-DLP-007; scenario SC-DLP-007-2.

        A program with the switch off is loaded with the option off, so no doubling shows.
        """
        self.program.weekend_double_points = False
        programs = self._loaded_programs()
        self.assertIn(self.program.id, programs)
        self.assertIn('weekend_double_points', programs[self.program.id])
        self.assertFalse(programs[self.program.id]['weekend_double_points'])

    def test_screen_shows_double_points_on_saturday(self):
        """Requirements: FR-DLP-007, NFR-REL-001; scenario SC-DLP-007-1.

        Saturday, 40 in products: the screen shows 80 points; after payment the card has 80.
        """
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour('WeekendPointsSaturdayTour')

    def test_screen_shows_normal_points_on_tuesday(self):
        """Requirements: FR-DLP-007; scenario SC-DLP-007-2.

        Tuesday, 40 in products: the screen shows 40 points.
        """
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour('WeekendPointsWeekdayTour')
